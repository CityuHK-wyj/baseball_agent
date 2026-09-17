"""Review-only reproductions for the v0.2 anti-shortcut audit.

This file is intentionally outside the product and test suites.  It uses injected
fakes and pure helpers so the findings do not depend on live services or fixtures.
"""

from datetime import date

from app.agent.agent import BaseballAgent, _Gathered
from app.agent.cognition import DeterministicCognition
from app.models.agent_runtime import CognitionPlan, EvidenceItem, LocalMetricHint
from app.tools.batting import BattingLine, BattingStatsTool, select_team
from app.tools.web_research import WebResearchTool, WebSearchResult


def status_shortcut():
    return BaseballAgent._status_for(_Gathered(evidence=[EvidenceItem(
        kind="WEB", summary="unrelated search result", source="search", reference="u",
        text="This says nothing about the requested 2025 Yankees pitching question.")]))


class RecordingBattingClient:
    def __init__(self):
        self.seasons = []
        self.ranges = []

    def season(self, year):
        self.seasons.append(year)
        return ()

    def date_range(self, start, end):
        self.ranges.append((start, end))
        return ()


def date_window_shortcut():
    client = RecordingBattingClient()
    agent = BaseballAgent(
        DeterministicCognition(), knowledge=None, entity_lookup=None,
        batting=BattingStatsTool(client=client), today=lambda: date(2026, 9, 17))
    gathered = _Gathered()
    agent._gather_batting(CognitionPlan(
        user_goal="last 30 days", needs_batting_stats=True,
        batting_year=2026, batting_entity_names=("Unseen Player",),
        batting_metrics=("OPS",)), gathered)
    return {"season_calls": client.seasons, "range_calls": client.ranges,
            "evidence": gathered.evidence[0].data if gathered.evidence else None}


def team_city_shortcut():
    lines = (
        BattingLine("Yankees hitter", "1", "New York", 100, 1, 1, 1, .2, .3, .4, .7),
        BattingLine("Mets hitter", "2", "New York", 100, 1, 1, 1, .2, .3, .4, .8),
        BattingLine("Cubs hitter", "3", "Chicago", 100, 1, 1, 1, .2, .3, .4, .7),
        BattingLine("White Sox hitter", "4", "Chicago", 100, 1, 1, 1, .2, .3, .4, .8),
    )
    return {
        "New York Yankees": [line.name for line in select_team(lines, "New York Yankees")],
        "Chicago Cubs": [line.name for line in select_team(lines, "Chicago Cubs")],
    }


def deterministic_phrase_special_case():
    cognition = DeterministicCognition()
    common = cognition.plan(message="How hard were pitches?", history="",
                            resolved_entities=("Unseen Player",), unknowns=(), today="2026-09-17")
    known_shape = cognition.plan(message="高区快速球", history="",
                                 resolved_entities=("Unseen Player",), unknowns=(), today="2026-09-17")
    return {"ordinary_local_metrics": len(common.local_metrics),
            "high_fastball_local_metrics": [item.metric for item in known_shape.local_metrics]}


class SearchBackend:
    def search(self, query):
        return (WebSearchResult(title="Unrelated title", url="https://example.test/unrelated",
                                snippet="Unrelated snippet"),)


class Reader:
    def read(self, url):
        return ""


def web_weak_evidence():
    item = WebResearchTool(backends=(SearchBackend(),), reader=Reader(), fetch_pages=1).research("new query")[0]
    return {"reference": item.reference, "text": item.text, "kind": item.kind,
            "accepted": item.accepted}


if __name__ == "__main__":
    print("status_shortcut", status_shortcut())
    print("date_window_shortcut", date_window_shortcut())
    print("team_city_shortcut", team_city_shortcut())
    print("deterministic_phrase_special_case", deterministic_phrase_special_case())
    print("web_weak_evidence", web_weak_evidence())
