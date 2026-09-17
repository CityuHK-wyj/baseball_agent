"""Real read-only Statcast analytics adapters (DuckDB/Parquet and PostgreSQL).

These tools turn a Requirement's *semantic* descriptor and typed analytical constraints
into one validated, read-only query. They consult the FieldMappingRegistry for physical
columns and named location definitions, so the Planner never reasons in column names and
a source never silently redefines a semantic request.

Both adapters are strictly read-only: SQL passes through the existing AST guard and
executes through the existing bounded read-only executors. They never gain write
capability through the Agent runtime.
"""

import json
from collections.abc import Iterable
from typing import Literal

from app.agent.executor import ToolResult
from app.models.artifacts import Artifact, Provenance
from app.models.contracts import (ArtifactRequirement, CategoryConstraint, CountConstraint,
                                  LocationConstraint, NumericConstraint, PitchTypeConstraint,
                                  PopulationConstraint, RankingConstraint, TimeRange)
from app.semantic.field_mapping import (BATTER_RELATIVE_UPPER_EDGE, FieldMappingRegistry,
                                        UPPER_EDGE_BAND_FEET)

# A stable, documented ranking qualification used only when neither the user nor the
# frozen Requirement supplies one. It is surfaced in the payload, never silently applied.
DEFAULT_MIN_BATTED_BALLS = 3

# MLB StatsAPI game-type codes retained on Statcast rows. Regular season is the narrow
# v0.1 default; postseason covers the Wild Card / Division / Championship / World Series
# codes and Spring Training its own code.
_GAME_TYPE_CODES: dict[str, tuple[str, ...]] = {
    "REGULAR_SEASON": ("R",),
    "POSTSEASON": ("F", "D", "L", "W"),
    "SPRING_TRAINING": ("S",),
    "EXHIBITION": ("E", "A"),
}


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class StatcastAnalyticsTool:
    """Shared SQL construction for the local Statcast sources."""

    name: str = "statcast"
    source_kind: Literal["POSTGRES", "PARQUET"] = "PARQUET"
    source_label: str = "local-statcast"

    def __init__(self, requirements: Iterable[ArtifactRequirement],
                 field_mapping: FieldMappingRegistry,
                 player_names: dict[str, str] | None = None,
                 min_batted_balls: int | None = None) -> None:
        self._by_id = {item.requirement_id: item for item in requirements}
        self._field_mapping = field_mapping
        self._player_names = dict(player_names or {})
        # Legacy fallback only; no adapter setting may override a frozen rule.
        self._min_batted_balls = min_batted_balls

    def execute(self, task) -> ToolResult:
        requirement = self._by_id.get(task.requirement_refs[0])
        if requirement is None:
            return ToolResult.no_data()
        try:
            ranking = self._ranking(requirement)
        except UnavailablePhysicalFields as error:
            return ToolResult.failure(f"{type(error).__name__}: {error}", retryable=False,
                                      error_code="MISSING_PHYSICAL_FIELDS")
        metric_field = self._field_mapping.physical_field(ranking.metric_key, self.source_kind)
        batter_field = self._field_mapping.physical_field("batter", self.source_kind)
        if metric_field is None or batter_field is None:
            return ToolResult.failure(
                f"No physical mapping for {ranking.metric_key}/batter in {self.source_kind}",
                retryable=False, error_code="NO_PHYSICAL_MAPPING")
        try:
            where, missing = self._where_clause(requirement, metric_field)
        except UnavailablePhysicalFields as error:
            return ToolResult.failure(
                f"{type(error).__name__}: {error}", retryable=False,
                error_code="MISSING_PHYSICAL_FIELDS")
        if missing:
            return ToolResult.failure(
                "Required physical fields are unavailable in this source: "
                + ", ".join(sorted(missing)), retryable=False,
                error_code="MISSING_PHYSICAL_FIELDS")
        min_batted_balls = self._qualification_threshold(requirement)
        population = self._population(requirement)

        rows = self._query(self._main_sql(requirement, where, metric_field, batter_field,
                                          ranking, min_batted_balls))
        if rows is None:
            return ToolResult.failure("Source query failed", retryable=True,
                                      error_code="SOURCE_QUERY_FAILED")
        observed = self._query(self._observed_sql(requirement, where))
        observed_range = self._observed_range(observed, requirement)

        names = dict(self._player_names)
        names.update(self._resolve_names([int(row[0]) for row in rows]))

        columns = ("batter", "batter_name", "batted_balls",
                   f"avg_{ranking.metric_key}_mph", f"max_{ranking.metric_key}_mph")
        projected = []
        for batter_id, count, avg_ev, max_ev in rows:
            batter_id_text = str(int(batter_id))
            projected.append([
                batter_id_text,
                names.get(batter_id_text, ""),
                int(count),
                round(float(avg_ev), 1),
                round(float(max_ev), 1),
            ])
        payload = json.dumps({
            "columns": list(columns),
            "rows": projected,
            "applied_constraints": [item.model_dump(mode="json") for item in requirement.descriptor.constraints],
            "min_batted_balls": min_batted_balls,
            "population": population.model_dump(mode="json") if population else None,
            "observed_time_range": observed_range.model_dump(mode="json") if observed_range else None,
            "source_kind": self.source_kind,
        }, ensure_ascii=False).encode()

        artifact = Artifact(
            artifact_id=f"{self.name}-{requirement.requirement_id}",
            descriptor=requirement.descriptor,
            payload_ref=f"{self.source_kind.casefold()}://{requirement.requirement_id}",
            provenance=Provenance(source=self.source_label, source_kind=self.source_kind,
                                  reference=self._reference()),
            row_count=len(projected),
            observed_time_range=observed_range,
        )
        return ToolResult.ok(len(projected), artifact=artifact, payload=payload)

    # -- Query construction -------------------------------------------------

    def _ranking(self, requirement: ArtifactRequirement) -> RankingConstraint:
        for constraint in requirement.descriptor.constraints:
            if isinstance(constraint, RankingConstraint):
                return constraint
        raise UnavailablePhysicalFields("no ranking constraint")

    def _qualification_threshold(self, requirement: ArtifactRequirement) -> int:
        rule = requirement.qualification_rule
        if rule is not None and rule.min_batted_balls is not None:
            return int(rule.min_batted_balls)
        if self._min_batted_balls is not None:
            return int(self._min_batted_balls)
        return DEFAULT_MIN_BATTED_BALLS

    def _population(self, requirement: ArtifactRequirement) -> PopulationConstraint | None:
        for constraint in requirement.descriptor.constraints:
            if isinstance(constraint, PopulationConstraint):
                return constraint
        return None

    def _resolve_names(self, batter_ids: list[int]) -> dict[str, str]:
        """Optional source-specific batter-name resolution. Base sources return {}."""
        return {}

    def _where_clause(self, requirement: ArtifactRequirement, metric_field: str) -> tuple[str, set[str]]:
        clauses: list[str] = []
        missing: set[str] = set()
        window = requirement.descriptor.time_range
        if window is not None:
            clauses.append(self._date_clause(window.start.isoformat(), window.end.isoformat()))
        if requirement.descriptor.entities:
            entities = requirement.descriptor.entities
            if any(entity.namespace != "MLBAM" or entity.entity_type != "PLAYER"
                   or not entity.identifier.isascii() or not entity.identifier.isdigit()
                   for entity in entities):
                raise UnavailablePhysicalFields("unsupported analytics entity identity")
            batter = self._field_mapping.physical_field("batter", self.source_kind)
            if batter is None:
                raise UnavailablePhysicalFields("batter identity field is unavailable")
            identifiers = ", ".join(str(int(entity.identifier)) for entity in entities)
            clauses.append(f"{batter} IN ({identifiers})")
        for constraint in requirement.descriptor.constraints:
            if isinstance(constraint, CountConstraint):
                strikes = self._field_mapping.physical_field("count", self.source_kind)
                balls = self._field_mapping.physical_field("balls", self.source_kind)
                if strikes is None or balls is None:
                    missing.update({"count", "balls"})
                    continue
                clauses.append(f"{strikes} = {int(constraint.strikes)}")
                if constraint.balls:
                    ball_list = ", ".join(str(int(ball)) for ball in sorted(constraint.balls))
                    clauses.append(f"{balls} IN ({ball_list})")
                else:
                    clauses.append("1 = 0")
            elif isinstance(constraint, NumericConstraint):
                field = self._field_mapping.physical_field(constraint.key, self.source_kind)
                if field is None:
                    missing.add(constraint.key)
                    continue
                operator = {"GT": ">", "GTE": ">=", "LT": "<", "LTE": "<=", "EQ": "="}[constraint.operator]
                clauses.append(f"{field} {operator} {float(constraint.value)}")
            elif isinstance(constraint, PitchTypeConstraint):
                field = self._field_mapping.physical_field("pitch_type", self.source_kind)
                codes = self._field_mapping.pitch_type_codes(constraint.family)
                if field is None or codes is None:
                    missing.add("pitch_type")
                    continue
                code_list = ", ".join(_quote(code) for code in codes)
                clauses.append(f"{field} IN ({code_list})")
            elif isinstance(constraint, LocationConstraint):
                self._apply_location(constraint, clauses, missing)
            elif isinstance(constraint, PopulationConstraint):
                self._apply_population(constraint, clauses, missing)
            elif isinstance(constraint, CategoryConstraint) and constraint.key == "date_range":
                if len(constraint.values) == 2:
                    clauses.append(self._date_clause(constraint.values[0], constraint.values[1]))
        clauses.append(f"{metric_field} IS NOT NULL")
        return (" AND ".join(clauses) if clauses else "1 = 1"), missing

    def _apply_population(self, constraint: PopulationConstraint, clauses: list[str],
                          missing: set[str]) -> None:
        """Apply the explicit population filter; never silently widen it."""
        game_field = self._physical_field("game_type")
        if game_field is None:
            missing.add("game_type")
        else:
            codes: list[str] = []
            for game_type in constraint.game_types:
                codes.extend(_GAME_TYPE_CODES.get(game_type, ()))
            if not codes:
                missing.add("game_type")
            else:
                code_list = ", ".join(_quote(code) for code in dict.fromkeys(codes))
                clauses.append(f"{game_field} IN ({code_list})")
        if constraint.event_population == "BATTED_BALL":
            if self._physical_field("description") is None:
                missing.add("description")
            else:
                # events marks a completed plate appearance, including walks,
                # strikeouts and truncated foul contacts. Use the pitch result.
                clauses.append("description = 'hit_into_play'")
        elif constraint.event_population == "MEASURED_CONTACT":
            field = self._field_mapping.physical_field("exit_velocity", self.source_kind)
            if field is None or not self._physical_column_available(field):
                missing.add("exit_velocity")
            else:
                # Contact measurement is independent of the metric being ranked.
                clauses.append(f"{field} IS NOT NULL")

    def _apply_location(self, constraint: LocationConstraint, clauses: list[str],
                        missing: set[str]) -> None:
        definition = self._field_mapping.location(constraint.definition)
        if definition is None:
            missing.add(f"pitch_location:{constraint.definition}")
            return
        unavailable = [field for field in definition.required_physical_fields
                       if not self._physical_column_available(field)]
        if unavailable:
            # Never silently degrade: the exact definition's fields are unavailable.
            raise UnavailablePhysicalFields(
                f"{constraint.definition} requires {', '.join(unavailable)}")
        if definition.predicate == "BATTER_RELATIVE_UPPER_EDGE":
            # plate_z, sz_top and sz_bot are guaranteed available above.
            clauses.append(f"plate_z >= sz_top - {UPPER_EDGE_BAND_FEET} AND plate_z <= sz_top")
            return
        if definition.zone_codes:
            zone_field = self._physical_field("zone")
            if zone_field is None:
                missing.add("zone")
                return
            zone_list = ", ".join(str(int(code)) for code in definition.zone_codes)
            clauses.append(f"{zone_field} IN ({zone_list})")

    def _physical_column_available(self, field: str) -> bool:
        return field in self._available_columns()

    def _physical_field(self, field: str) -> str | None:
        # A direct physical column is already a physical name; availability is checked
        # separately. Used for zone.
        return field if field in self._available_columns() else None

    def _date_clause(self, start: str, end: str) -> str:
        field = self._field_mapping.physical_field("game_date", self.source_kind) or "game_date"
        if self.source_kind == "PARQUET":
            return f"{field} >= DATE '{start}' AND {field} <= DATE '{end}'"
        return f"{field} >= DATE '{start}' AND {field} <= DATE '{end}'"

    # -- Source-specific pieces ---------------------------------------------

    def _available_columns(self) -> frozenset[str]:
        raise NotImplementedError

    def _reference(self) -> str:
        raise NotImplementedError

    def _query(self, sql: str) -> list[tuple] | None:
        raise NotImplementedError

    def _main_sql(self, requirement, where, metric_field, batter_field,
                  ranking: RankingConstraint, min_batted_balls: int) -> str:
        direction = "DESC" if ranking.direction == "DESC" else "ASC"
        aggregation = ranking.aggregation
        return (
            f"SELECT {batter_field} AS batter, COUNT(*) AS n, "
            f"AVG({metric_field}) AS avg_metric, MAX({metric_field}) AS max_metric "
            f"FROM {self._from_clause()} WHERE {where} "
            f"GROUP BY {batter_field} HAVING COUNT(*) >= {int(min_batted_balls)} "
            f"ORDER BY {aggregation}({metric_field}) {direction} LIMIT {int(ranking.limit)}"
        )

    def _observed_sql(self, requirement, where) -> str:
        """Observed coverage is the source's data span for the requested window, not the
        span of qualifying events (which is naturally sparse for filtered rankings)."""
        field = self._field_mapping.physical_field("game_date", self.source_kind) or "game_date"
        if requirement.descriptor.time_range is not None:
            start = requirement.descriptor.time_range.start.isoformat()
            end = requirement.descriptor.time_range.end.isoformat()
            return (f"SELECT MIN({field}), MAX({field}) FROM {self._from_clause()} "
                    f"WHERE {field} >= DATE '{start}' AND {field} <= DATE '{end}'")
        return f"SELECT MIN({field}), MAX({field}) FROM {self._from_clause()}"

    def _from_clause(self) -> str:
        raise NotImplementedError

    @staticmethod
    def _observed_range(observed, requirement: ArtifactRequirement) -> TimeRange | None:
        if not observed or observed[0][0] is None or observed[0][1] is None:
            return requirement.descriptor.time_range
        start, end = observed[0][0], observed[0][1]
        import datetime as _dt
        return TimeRange(start=start.date() if isinstance(start, _dt.datetime) else start,
                         end=end.date() if isinstance(end, _dt.datetime) else end)


class UnavailablePhysicalFields(Exception):
    pass


class ParquetStatcastTool(StatcastAnalyticsTool):
    """Read-only analytics against the historical Parquet archive through DuckDB."""

    name = "statcast-parquet"
    source_kind = "PARQUET"
    source_label = "parquet-archive"

    _COLUMNS = frozenset({
        "game_date", "game_pk", "game_type", "release_speed", "release_spin_rate",
        "pitch_type", "player_name", "pitcher", "batter", "events", "description",
        "plate_x", "plate_z", "sz_top", "sz_bot", "p_throws", "stand", "balls", "strikes",
        "zone", "inning", "launch_speed", "launch_angle", "hit_distance_sc",
        "estimated_ba_using_speedangle", "estimated_woba_using_speedangle",
    })

    def __init__(self, requirements, field_mapping: FieldMappingRegistry,
                 executor, archive_glob: str = "mlb_statcast_*.parquet",
                 player_names: dict[str, str] | None = None,
                 min_batted_balls: int | None = None) -> None:
        super().__init__(requirements, field_mapping, player_names, min_batted_balls)
        self._executor = executor
        self._archive_glob = archive_glob

    def _available_columns(self) -> frozenset[str]:
        return self._COLUMNS

    def _reference(self) -> str:
        return f"parquet:{self._archive_glob}"

    def _from_clause(self) -> str:
        return f"read_parquet({_quote(self._archive_glob)})"

    def _query(self, sql: str) -> list[tuple] | None:
        result, rows = self._executor.execute_with_rows(sql)
        if result.status != "OK":
            return None
        return list(rows)


class PostgresStatcastTool(StatcastAnalyticsTool):
    """Read-only analytics against the hot PostgreSQL store."""

    name = "statcast-postgres"
    source_kind = "POSTGRES"
    source_label = "postgres-baseball_analytics"

    _COLUMNS = frozenset({
        "game_date", "game_pk", "game_type", "release_speed", "release_spin_rate",
        "pitch_type", "player_name", "pitcher_id", "batter_id", "events", "description",
        "plate_x", "plate_z", "sz_top", "sz_bot", "p_throws", "stand", "balls", "strikes",
        "zone", "inning", "launch_speed", "launch_angle", "hit_distance_sc",
        "estimated_ba_using_speedangle", "estimated_woba_using_speedangle",
    })

    def __init__(self, requirements, field_mapping: FieldMappingRegistry,
                 executor, table: str = "statcast_pitches",
                 name_table: str | None = "player_dictionary",
                 player_names: dict[str, str] | None = None,
                 min_batted_balls: int | None = None) -> None:
        super().__init__(requirements, field_mapping, player_names, min_batted_balls)
        self._executor = executor
        self._table = table
        self._name_table = name_table

    def _available_columns(self) -> frozenset[str]:
        return self._COLUMNS

    def _reference(self) -> str:
        return f"postgres:{self._table}"

    def _from_clause(self) -> str:
        return self._table

    def _query(self, sql: str) -> list[tuple] | None:
        result, rows = self._executor.execute_with_rows(sql)
        if result.status != "OK":
            return None
        return list(rows)

    def _resolve_names(self, batter_ids: list[int]) -> dict[str, str]:
        if not batter_ids or not self._name_table:
            return {}
        id_list = ", ".join(str(int(item)) for item in batter_ids)
        result, rows = self._executor.execute_with_rows(
            f"SELECT player_id, player_name FROM {self._name_table} WHERE player_id IN ({id_list})")
        if result.status != "OK":
            return {}
        return {str(int(player_id)): name for player_id, name in rows if name}
