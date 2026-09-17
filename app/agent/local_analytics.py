"""Compile a free-form local analytical hint into the strict SQL boundary and execute.

This is the *only* place where the LLM-first runtime touches privileged analytics. The
hint is never executable: metrics, locations, populations and entities are validated
against trusted registries, then a typed ``ArtifactRequirement`` is built and handed to
the existing read-only Statcast adapters (which compile a ``SQLAnalysisRequest``).

A compilation failure returns a structured recovery code; it never fails the answer.
"""

from dataclasses import dataclass, field
from datetime import date

from app.config import settings
from app.models.agent_runtime import EvidenceItem, LocalMetricHint
from app.models.contracts import (ArtifactDescriptor, ArtifactRequirement, Entity,
                                  LocationConstraint, PitchTypeConstraint,
                                  PopulationConstraint, QualificationRule, RankingConstraint,
                                  TimeRange)
from app.models.planning import AgentTask
from app.semantic.field_mapping import DEFAULT_LOCATION_DEFINITIONS, FieldMappingRegistry
from app.tools.execution import DuckDBReadOnlyExecutor, PostgresReadOnlyExecutor
from app.tools.statcast import ParquetStatcastTool, PostgresStatcastTool

_LOCAL_METRICS = frozenset({"exit_velocity", "pitch_velocity"})
_AGGREGATIONS = frozenset({"AVG", "MAX", "MIN", "SUM"})
_DIRECTIONS = frozenset({"ASC", "DESC"})
_GAME_TYPES = frozenset({"REGULAR_SEASON", "POSTSEASON", "SPRING_TRAINING", "EXHIBITION"})
_EVENT_POPULATIONS = frozenset({"BATTED_BALL", "MEASURED_CONTACT", "ALL_PITCHES"})
_LOCATION_NAMES = {item.definition for item in DEFAULT_LOCATION_DEFINITIONS}
_PITCH_FAMILIES = frozenset({"fastball", "breaking", "offspeed"})

# Declared local coverage windows (kept in sync with the router capabilities).
_PARQUET_COVERAGE = (date(2015, 4, 5), date(2023, 11, 1))
_POSTGRES_COVERAGE = (date(2024, 3, 15), date(2026, 9, 14))


@dataclass(frozen=True)
class LocalAnalyticsOutcome:
    evidence: EvidenceItem | None = None
    recovery_code: str = ""
    detail: str = ""
    sql_request: str = ""
    caveats: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.evidence is not None


class LocalAnalyticsRunner:
    def __init__(self, field_mapping: FieldMappingRegistry | None = None,
                 player_names: dict[str, str] | None = None,
                 postgres_enabled: bool = True,
                 parquet_glob: str | None = None) -> None:
        self._field_mapping = field_mapping or FieldMappingRegistry()
        self._player_names = dict(player_names or {})
        self._postgres_enabled = postgres_enabled and bool(settings.postgres_password)
        self._parquet_glob = parquet_glob or str(
            settings.parquet_archive_path / "mlb_statcast_*.parquet")

    def execute(self, hint: LocalMetricHint) -> LocalAnalyticsOutcome:
        metric = (hint.metric or "").strip().lower()
        if metric not in _LOCAL_METRICS:
            return LocalAnalyticsOutcome(
                recovery_code="UNKNOWN_LOCAL_METRIC",
                detail=f"'{hint.metric}' is not available from local analytics")
        aggregation = (hint.aggregation or "AVG").upper()
        direction = (hint.direction or "DESC").upper()
        aggregation = aggregation if aggregation in _AGGREGATIONS else "AVG"
        direction = direction if direction in _DIRECTIONS else "DESC"
        limit = hint.limit if 1 <= hint.limit <= 1000 else 10

        caveats: list[str] = []
        constraints: list = []
        constraints.append(RankingConstraint(metric_key=metric, aggregation=aggregation,
                                             direction=direction, limit=limit))
        if hint.pitch_family and hint.pitch_family.lower() in _PITCH_FAMILIES:
            constraints.append(PitchTypeConstraint(family=hint.pitch_family.lower()))
        elif hint.pitch_family:
            caveats.append(f"pitch family '{hint.pitch_family}' is unsupported; ignored")
        if hint.location_definition:
            if hint.location_definition in _LOCATION_NAMES:
                constraints.append(LocationConstraint(definition=hint.location_definition))
            else:
                caveats.append(
                    f"location '{hint.location_definition}' is not a registry definition; ignored")
        game_types = tuple(item for item in hint.game_types if item in _GAME_TYPES)
        event_population = (hint.event_population
                            if hint.event_population in _EVENT_POPULATIONS else None)
        if game_types or event_population:
            constraints.append(PopulationConstraint(
                game_types=game_types or ("REGULAR_SEASON",),
                event_population=event_population or "BATTED_BALL"))

        entities = tuple(
            Entity(namespace="MLBAM", entity_type="PLAYER", identifier=str(item))
            for item in hint.entity_ids if str(item).isdigit())
        time_range = _time_range(hint.start, hint.end)
        source_kind = self._source_for(time_range, caveats)
        if source_kind is None:
            return LocalAnalyticsOutcome(
                recovery_code="INSUFFICIENT_COVERAGE",
                detail=f"no local source covers {hint.start}..{hint.end}",
                caveats=tuple(caveats))

        requirement = ArtifactRequirement(
            requirement_id=f"local-{abs(hash((metric, aggregation, hint.start, hint.end, entities))) & 0xFFFFFF}",
            objective_ref="runtime",
            description=f"{aggregation} {metric} for {hint.note or 'requested population'}",
            descriptor=ArtifactDescriptor(
                artifact_type="TABLE", entities=entities, data_keys=(metric, "batter"),
                constraints=tuple(constraints), granularity="player_rank",
                time_range=time_range, population_scope="player" if entities else "league"),
            qualification_rule=(QualificationRule(kind="CUSTOM",
                                                  min_batted_balls=int(hint.min_batted_balls))
                                if hint.min_batted_balls and hint.min_batted_balls > 0 else None))
        task = AgentTask(task_id="runtime-task", objective_ref="runtime",
                         requirement_refs=(requirement.requirement_id,),
                         description=requirement.description, task_type="LOCAL_ANALYTICS")
        tool = self._build_tool(source_kind, requirement)
        result = tool.execute(task)
        if result.status != "OK" or result.artifact is None or result.payload is None:
            return LocalAnalyticsOutcome(
                recovery_code=result.error_code or "UNSUPPORTED_LOCAL_ANALYTICS",
                detail=result.safe_error_summary or "local analytics produced no usable result",
                caveats=tuple(caveats))
        import json
        payload = json.loads(result.payload.decode())
        compiled = payload.get("compiled_sql_request", {})
        rows = payload.get("rows", [])
        if not rows:
            caveats.append("no qualifying rows for this window/filters")
        evidence = EvidenceItem(
            kind="LOCAL_ANALYTICS",
            summary=f"{aggregation} {metric} ranking from {source_kind}" + (
                " (no qualifying rows)" if not rows else ""),
            source=f"statcast-{source_kind.lower()}",
            reference=f"sql:{compiled.get('request_id', requirement.requirement_id)}",
            text=_render(payload.get("columns", []), rows),
            data={"columns": payload.get("columns", []), "rows": payload.get("rows", []),
                  "min_batted_balls": payload.get("min_batted_balls"),
                  "observed_time_range": payload.get("observed_time_range"),
                  "source_kind": source_kind},
            accepted=True)
        return LocalAnalyticsOutcome(evidence=evidence,
                                     sql_request=json.dumps(compiled, ensure_ascii=False),
                                     caveats=tuple(caveats))

    def _source_for(self, window: TimeRange | None, caveats: list[str]) -> str | None:
        if window is None:
            return "POSTGRES" if self._postgres_enabled else "PARQUET"
        parquet_overlap = _overlap_days(window, *_PARQUET_COVERAGE)
        postgres_overlap = _overlap_days(window, *_POSTGRES_COVERAGE) if self._postgres_enabled else 0
        if parquet_overlap <= 0 and postgres_overlap <= 0:
            return None
        if parquet_overlap >= postgres_overlap:
            if window.end > _PARQUET_COVERAGE[1]:
                caveats.append(
                    f"window extends beyond Parquet coverage; using PARQUET through "
                    f"{_PARQUET_COVERAGE[1].isoformat()}")
            return "PARQUET"
        if window.start < _POSTGRES_COVERAGE[0]:
            caveats.append(
                f"window starts before PostgreSQL coverage; using POSTGRES from "
                f"{_POSTGRES_COVERAGE[0].isoformat()}")
        return "POSTGRES"

    def _build_tool(self, source_kind: str, requirement: ArtifactRequirement):
        if source_kind == "PARQUET":
            return ParquetStatcastTool([requirement], self._field_mapping,
                                       DuckDBReadOnlyExecutor(settings.parquet_archive_path),
                                       archive_glob=self._parquet_glob,
                                       player_names=self._player_names)
        return PostgresStatcastTool([requirement], self._field_mapping,
                                    PostgresReadOnlyExecutor(settings, allowed_tables=(
                                        "statcast_pitches", "player_dictionary")),
                                    player_names=self._player_names)


def _overlap_days(window: TimeRange, start: date, end: date) -> int:
    overlap_start = max(window.start, start)
    overlap_end = min(window.end, end)
    return max((overlap_end - overlap_start).days + 1, 0)


def _time_range(start: str | None, end: str | None) -> TimeRange | None:
    if not start or not end:
        return None
    try:
        return TimeRange(start=date.fromisoformat(start), end=date.fromisoformat(end))
    except ValueError:
        return None


def _render(columns, rows) -> str:
    if not columns or not rows:
        return ""
    header = " | ".join(str(item) for item in columns)
    body = [" | ".join("" if cell is None else str(cell) for cell in row) for row in rows]
    return "\n".join([header, *body])
