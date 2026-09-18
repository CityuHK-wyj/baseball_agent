"""Diagnostic scenarios for the v0.5 runtime-performance audit.

Scenarios are expressed as *capability classes*, never as production prompt literals.
They are used only by the diagnostic runners in this directory and are never imported by
production code.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from app.models.agent_runtime import EvidenceItem
from app.models.artifact_runtime import Need, Scope, SemanticBrief
from app.models.contracts import TimeRange

TODAY = date(2025, 9, 30)
PARQUET_ROOT = Path("data_loader/parquet_archive")
PARQUET_GLOB = str(PARQUET_ROOT / "mlb_statcast_*.parquet")


# ---------------------------------------------------------------------------
# Semantic briefs
# ---------------------------------------------------------------------------


class FixedInterpreter:
    """Deterministic interpreter for scripted scenarios: message -> configured brief."""

    def __init__(self, brief_factory=None, question: str = "", options=()) -> None:
        self._factory = brief_factory
        self._question = question
        self._options = tuple(options)

    def brief(self, *, message, history, resolved_entities, unknowns, today):
        base = (self._factory(message) if self._factory else
                SemanticBrief(brief_id="brief-fixed", goal_statement=message,
                              understanding=message, source="diagnostic"))
        return base.model_copy(update={
            "brief_id": "brief-fixed", "goal_statement": base.goal_statement or message,
            "clarification_question": self._question,
            "clarification_options": self._options})


def scope_window(start: str, end: str, **kwargs) -> Scope:
    return Scope(time_range=TimeRange(start=date.fromisoformat(start),
                                      end=date.fromisoformat(end)), **kwargs)


# ---------------------------------------------------------------------------
# Safe Analytical IR builders (catalog-valid identifiers only)
# ---------------------------------------------------------------------------


def ir_pitch_count(*, start: str, end: str, limit: int = 5,
                   entity_set: dict | None = None) -> dict:
    ir = {
        "query_id": "q-count", "source_kind": "PARQUET", "table": "mlb_statcast_archive",
        "selections": [
            {"alias": "pitcher", "kind": "GROUP_KEY", "field": "pitcher"},
            {"alias": "pitches", "kind": "AGGREGATE",
             "aggregate": {"op": "COUNT", "alias": "pitches"}},
        ],
        "date_field": "game_date",
        "window": {"start": start, "end": end},
        "order_by": "pitches", "direction": "DESC", "limit": limit,
    }
    if entity_set is not None:
        ir["entity_set"] = entity_set
    return ir


def ir_avg_velocity_by_pitcher(*, start: str, end: str, limit: int = 5) -> dict:
    return {
        "query_id": "q-avg", "source_kind": "PARQUET", "table": "mlb_statcast_archive",
        "selections": [
            {"alias": "pitcher", "kind": "GROUP_KEY", "field": "pitcher"},
            {"alias": "avg_velo", "kind": "AGGREGATE",
             "aggregate": {"op": "AVG", "field": "release_speed", "alias": "avg_velo"}},
        ],
        "date_field": "game_date",
        "window": {"start": start, "end": end},
        "order_by": "avg_velo", "direction": "DESC", "limit": limit,
    }


def ir_derived_ratio(*, start: str, end: str, limit: int = 5) -> dict:
    """Two aggregates plus a derived PCT (used to exercise Compute)."""
    return {
        "query_id": "q-derived", "source_kind": "PARQUET", "table": "mlb_statcast_archive",
        "selections": [
            {"alias": "pitcher", "kind": "GROUP_KEY", "field": "pitcher"},
            {"alias": "n", "kind": "AGGREGATE",
             "aggregate": {"op": "COUNT", "alias": "n"}},
            {"alias": "hard", "kind": "AGGREGATE",
             "aggregate": {"op": "COUNT_IF", "field": "launch_speed", "alias": "hard",
                           "condition": {"kind": "COMPARE", "field": "launch_speed",
                                         "operator": "GTE", "value": 95}}},
            {"alias": "hard_rate", "kind": "DERIVED",
             "expression": {"op": "PCT",
                            "left": {"op": "AGG", "alias": "hard"},
                            "right": {"op": "AGG", "alias": "n"}}},
        ],
        "date_field": "game_date",
        "window": {"start": start, "end": end},
        "order_by": "hard_rate", "direction": "DESC", "limit": limit,
    }


def ir_invalid_field(*, start: str, end: str) -> dict:
    return {
        "query_id": "q-bad", "source_kind": "PARQUET", "table": "mlb_statcast_archive",
        "selections": [
            {"alias": "x", "kind": "AGGREGATE",
             "aggregate": {"op": "AVG", "field": "invented_velocity", "alias": "x"}},
        ],
        "date_field": "game_date",
        "window": {"start": start, "end": end},
    }


def ir_postgres_count(*, start: str, end: str) -> dict:
    return {
        "query_id": "q-pg", "source_kind": "POSTGRES", "table": "statcast_pitches",
        "selections": [
            {"alias": "pitcher", "kind": "GROUP_KEY", "field": "pitcher"},
            {"alias": "pitches", "kind": "AGGREGATE",
             "aggregate": {"op": "COUNT", "alias": "pitches"}},
        ],
        "date_field": "game_date",
        "window": {"start": start, "end": end},
        "order_by": "pitches", "direction": "DESC", "limit": 5,
    }


def ir_empty_window(*, start: str, end: str) -> dict:
    return ir_pitch_count(start=start, end=end)


# ---------------------------------------------------------------------------
# Need builders
# ---------------------------------------------------------------------------


def need(need_id, capability, *, parameters=None, depends_on=(), scope=None,
         criticality="CORE", produces=()):
    return Need(need_id=need_id, objective=f"diagnostic need {need_id}",
                expected_information=f"diagnostic expected information {need_id}",
                proposed_capability=capability, preferred_capabilities=tuple(produces),
                parameters=dict(parameters or {}), depends_on=tuple(depends_on),
                required_scope=scope, criticality=criticality)


def needs_local_count(*, start="2021-06-01", end="2021-06-30", entity_set=None,
                      scope=None):
    return [need("need-local", "local_analytics",
                 parameters={"analytical_query": ir_pitch_count(
                     start=start, end=end, entity_set=entity_set),
                     "start": start, "end": end},
                 scope=scope or Scope(population="players"))]


def needs_roster_then_local(*, team="New York Yankees", start="2021-06-01",
                            end="2021-06-30"):
    return [
        need("need-roster", "roster", parameters={"team": team},
             scope=Scope(entities=(team,), population="players")),
        need("need-local", "local_analytics",
             parameters={"analytical_query": ir_pitch_count(
                 start=start, end=end,
                 entity_set={"field": "batter", "export_ref": "need-roster"}),
                 "start": start, "end": end},
             depends_on=("need-roster",),
             scope=Scope(population="players")),
    ]


def needs_compute(*, start="2021-06-01", end="2021-06-30"):
    return [
        need("need-local", "local_analytics",
             parameters={"analytical_query": ir_derived_ratio(start=start, end=end),
                         "start": start, "end": end},
             scope=Scope(population="players")),
        need("need-compute", "compute", parameters={"op": "MEAN"},
             depends_on=("need-local",)),
    ]


def needs_knowledge(*, query="designated for assignment"):
    return [need("need-knowledge", "shared_knowledge", parameters={"query": query})]


def needs_entity_resolution(*, mentions):
    return [need("need-entities", "entity_resolution",
                 parameters={"mentions": list(mentions)})]


def needs_web_then_entities(*, query="recent pitcher injuries", focus):
    return [
        need("need-web", "web_research", parameters={"query": query}),
        need("need-entities", "evidence_entities", parameters={"focus": list(focus)},
             depends_on=("need-web",)),
    ]


def needs_web(*, query="recent MLB trade news"):
    return [need("need-web", "web_research", parameters={"query": query})]


def needs_local_then_web(*, start="2021-06-01", end="2021-06-30",
                         query="context for these pitchers"):
    return [
        need("need-local", "local_analytics",
             parameters={"analytical_query": ir_pitch_count(start=start, end=end),
                         "start": start, "end": end},
             scope=Scope(population="players")),
        need("need-web", "web_research", parameters={"query": query},
             depends_on=("need-local",)),
    ]


def needs_impossible():
    return [need("need-ghost", "ghost_capability", parameters={"query": "anything"})]


def needs_batting(*, season=2021, names=()):
    return [need("need-batting", "batting_stats",
                 parameters={"season": season, "metric": "OPS", "names": list(names)})]


# ---------------------------------------------------------------------------
# Fake web backend (deterministic grounding)
# ---------------------------------------------------------------------------


class FakeWeb:
    """Deterministic web backend with explicit per-result grounding."""

    def __init__(self, findings=(), grounded=True):
        self._findings = findings
        self._grounded = grounded
        self.queries: list[str] = []

    def research(self, query, *, fetch_pages=None):
        self.queries.append(query)
        items = []
        for index, finding in enumerate(self._findings):
            url = finding.get("url", f"https://example.test/{index}")
            items.append(EvidenceItem(
                kind="WEB", summary=finding.get("title", query), source="example.test",
                reference=url, text=finding.get("text", ""),
                data={"query": query, "snippet": finding.get("snippet", ""),
                      "title": finding.get("title", query), "url": url,
                      "fetched": bool(finding.get("fetched", self._grounded))},
                accepted=bool(finding.get("fetched", self._grounded)),
                retrieved_at=datetime.now(timezone.utc)))
        return tuple(items)

    def search(self, query):
        return ()


def grounded_named_findings(names):
    """Grounded evidence text that mentions specific canonical player names."""
    text = ". ".join(f"{name} appeared in the report" for name in names)
    return ({"url": "https://example.test/report", "title": "Report",
             "text": text + "."},)


def grounded_player_findings():
    return (
        {"url": "https://example.test/a", "title": "Injury report",
         "text": "Jose Altuve was placed on the injured list. Freddie Freeman "
                 "returned to the lineup."},
        {"url": "https://example.test/b", "title": "Roster notes",
         "text": "Gerrit Cole is scheduled to start. Chris Sale is day to day."},
    )


class RecoveringPlanner:
    """Diagnostic planner that replaces a failed analytical Need with a corrected one.

    Used only to exercise the runtime's recovery path deterministically. It is not a
    production planner and does not change any production module.
    """

    def __init__(self, initial, corrected):
        from app.artifact_runtime.planner import ScriptedPlanner

        self._initial = tuple(initial)
        self._corrected = tuple(corrected)
        self._delegate = ScriptedPlanner(self._initial)
        self._recovered = False

    def initial_needs(self, *, goal, brief, context):
        return self._initial

    def add_needs(self, *, goal, brief, existing, artifacts, gaps, context):
        if self._recovered or not gaps:
            return ()
        self._recovered = True
        return self._corrected

    def next_action(self, *, goal, needs, artifacts, context):
        from app.artifact_runtime.planner import DeterministicPlanner

        return DeterministicPlanner().next_action(goal=goal, needs=needs,
                                                  artifacts=artifacts, context=context)


# ---------------------------------------------------------------------------
# Reference parquet player ids (used to make composition non-empty)
# ---------------------------------------------------------------------------


def parquet_batter_ids(limit: int = 5, year: int = 2021) -> tuple[int, ...]:
    import duckdb

    safe = PARQUET_GLOB.replace("'", "''")
    con = duckdb.connect(database=":memory:")
    try:
        rows = con.execute(
            "SELECT batter, COUNT(*) c FROM read_parquet(?) "
            "WHERE year(game_date)=? GROUP BY batter ORDER BY c DESC LIMIT ?",
            [safe, year, limit]).fetchall()
    finally:
        con.close()
    return tuple(int(row[0]) for row in rows)


def roster_provider_from_ids(ids: tuple[int, ...], team: str = "New York Yankees"):
    def provider(requested: str):
        if requested != team:
            from app.artifact_runtime.roster import RosterUnavailable
            raise RosterUnavailable(f"no roster {requested!r}")
        return tuple({"player_id": str(pid), "name": f"Player {pid}",
                      "team_id": "147", "team_name": team, "position": "P"}
                     for pid in ids)
    return provider
