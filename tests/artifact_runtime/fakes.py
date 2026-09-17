"""Shared fakes for artifact-runtime tests. No network, no real database."""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.knowledge.service import KnowledgeBase
from app.knowledge.store import SqliteKnowledgeStore
from app.models.agent_runtime import EvidenceItem
from app.models.artifact_runtime import Need, SemanticBrief
from app.models.knowledge import KnowledgeItem
from app.artifact_runtime.roster import RosterUnavailable
from app.semantic.entity_resolver import EntityDictionary, EntityResolver
from app.tools.entity_lookup import EntityLookup
from app.tools.results import ToolResult


def knowledge_base(items=()) -> KnowledgeBase:
    store = SqliteKnowledgeStore(":memory:")
    for item in items:
        store.upsert_item(item)
    return KnowledgeBase(store)


def dfa_item() -> KnowledgeItem:
    return KnowledgeItem(
        knowledge_id="TERM:DFA", canonical_key="dfa", knowledge_type="TERM",
        title="Designated for Assignment", aliases=("DFA",),
        summary="A roster move that removes a player from the 40-man roster.",
        source_authority="OFFICIAL")


def entity_lookup() -> EntityLookup:
    dictionary = EntityDictionary()
    return EntityLookup(dictionary, EntityResolver(dictionary))


class FakeWeb:
    """Web backend whose grounding is explicit per result."""

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


class RecordingExecutor:
    def __init__(self, rows=(), status="OK"):
        self.rows = tuple(rows)
        self.status = status
        self.statements: list[str] = []

    def execute_with_rows(self, sql):
        self.statements.append(sql)
        if not self.rows:
            return ToolResult.no_data(), ()
        return ToolResult.ok(len(self.rows)), self.rows


class FakeBattingClient:
    def __init__(self, season_lines=(), range_lines=None):
        self._season = tuple(season_lines)
        self._range = tuple(season_lines if range_lines is None else range_lines)
        self.season_calls: list[int] = []
        self.range_calls: list[tuple[str, str]] = []

    def season(self, year):
        self.season_calls.append(year)
        return self._season

    def date_range(self, start, end):
        self.range_calls.append((start, end))
        return self._range


def fake_roster(team):
    rosters = {
        "New York Yankees": (
            {"player_id": "660271", "name": "A Hitter", "team_id": "147",
             "team_name": "New York Yankees", "position": "OF"},
            {"player_id": "123456", "name": "B Hitter", "team_id": "147",
             "team_name": "New York Yankees", "position": "IF"},
        ),
        "New York Mets": (
            {"player_id": "999999", "name": "C Hitter", "team_id": "121",
             "team_name": "New York Mets", "position": "P"},
        ),
    }
    if team in rosters:
        return rosters[team]
    raise RosterUnavailable(f"no roster {team!r}")


class ScriptedInterpreter:
    """Returns a fixed brief so tests exercise the planner, not the semantic model."""

    def __init__(self, brief: SemanticBrief | None = None):
        self._brief = brief
        self.calls: list[dict] = []

    def brief(self, *, message, history, resolved_entities, unknowns, today):
        self.calls.append({"message": message, "history": history})
        if self._brief is not None:
            return self._brief.model_copy(update={"goal_statement": message})
        from app.artifact_runtime.planner import RuleBasedSemanticInterpreter
        return RuleBasedSemanticInterpreter().brief(
            message=message, history=history, resolved_entities=resolved_entities,
            unknowns=unknowns, today=today)


def need(need_id, capability, *, produces=(), parameters=None, depends_on=(),
         scope=None, criticality="CORE"):
    return Need(need_id=need_id, objective=need_id, expected_information=need_id,
                proposed_capability=capability, preferred_capabilities=tuple(produces),
                parameters=dict(parameters or {}), depends_on=tuple(depends_on),
                required_scope=scope, criticality=criticality)
