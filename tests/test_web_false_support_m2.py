"""M2-4: false-support pre-admission filtering with a Finding -> Span -> Document -> Judge trace.

The independent M1 review found real cases where subject-relevant Wikipedia citation/metadata
fragments were admitted as SUPPORTED. These tests trace the whole path and assert that an
obvious subject-only citation fragment never becomes an accepted claim, while the genuine
prose sentence in the same document still can.
"""

from __future__ import annotations

from app.capabilities.web_research import WebResearchTool
from app.capabilities.web_search import FetchedPage
from app.config import WebConfig
from tools.requirement_ancestry_fixture import Goal
from app.contracts.models import (
    PlannerNeed,
    PropositionKind,
    RequirementKind,
    ToolRequest,
    UserRequirement,
)
from app.verification.judge import Judge
from app.verification.propositions import build_propositions

CITATION_FRONT_MATTER = (
    "Aaron Judge awards and links\n"
    "Link to Rookie of the Month\n"
    "Link to Aaron Judge quotes\n"
    "Link to prior winners\n"
    "Link to Aaron Judge video\n"
)
GENUINE_FACT = "Aaron Judge was the subject of a new injury report about a calf strain."


class _Hit:
    def __init__(self, url, title, engine="fixture"):
        self.url = url
        self.title = title
        self.snippet = ""
        self.engine = engine


class _Backend:
    name = "fixture"

    def __init__(self, hits):
        self._hits = list(hits)
        self.last_error = ""

    def search(self, query, *, limit=5, language="en"):
        return list(self._hits)


def _run_web(monkeypatch, text, proposition, *, objective="find recent injury reports about Aaron Judge", llm=None):
    from tests.assessment_fixtures import FixtureAssessorLLM

    tool = WebResearchTool(WebConfig(broad_search=False), llm=llm or FixtureAssessorLLM())
    tool.backends = [_Backend([_Hit("https://example.com/judge", "Aaron Judge injury report")])]

    def fake_fetch(url, config):
        return FetchedPage(
            url=url, final_url=url, title="Aaron Judge injury report", text=text,
            retrieved_at="2026-09-01T00:00:00Z", ok=True, content_hash="hash-judge",
        )

    monkeypatch.setattr("app.capabilities.web_research.fetch_page", fake_fetch)
    request = ToolRequest(
        id="tr_n_web",
        need_id="n_web",
        capability="web_research",
        objective=objective,
        inputs={
            "entity_names": ["Aaron Judge"],
            "objective": objective,
            "user_wording": "",
            "propositions": [proposition.model_dump(exclude={"coverage"})],
        },
    )
    return tool.execute(request)


def _injury_proposition():
    from app.contracts.models import AnswerProposition

    return AnswerProposition(
        statement="find recent injury reports about Aaron Judge",
        kind=PropositionKind.FACT,
        subject="Aaron Judge",
        required_information="injury report",
        mandatory=True,
    )


def test_subject_only_citation_fragment_is_not_supported(monkeypatch):
    text = CITATION_FRONT_MATTER + "\n" + GENUINE_FACT
    result = _run_web(monkeypatch, text, _injury_proposition())
    data = result.artifact.structured_data
    document = data["documents"][0]
    # The full body is preserved for inspection.
    assert "Link to Rookie of the Month" in document["body_text"]
    # Every persisted span still satisfies the exact offset invariant.
    for span in data["evidence_spans"]:
        assert document["body_text"][span["start"] : span["end"]] == span["text"]
    supported = [f for f in data["evidence_findings"] if f["status"] == "SUPPORTED"]
    assert supported, "the genuine prose sentence must still support the proposition"
    for finding in supported:
        span = next(s for s in data["evidence_spans"] if s["id"] == finding["span_id"])
        assert "injury report" in span["text"]
        # A subject-only citation fragment must never be the supported passage.
        assert "Link to" not in span["text"]


def test_subject_only_citation_fragment_is_not_even_selected_as_candidate(monkeypatch):
    text = CITATION_FRONT_MATTER + "\n" + GENUINE_FACT
    result = _run_web(monkeypatch, text, _injury_proposition())
    data = result.artifact.structured_data
    # The selector excludes subject-only citation fragments before verification, so every
    # selected candidate is real prose carrying the requested relationship.
    assert data["evidence_spans"]
    for span in data["evidence_spans"]:
        assert span.get("quality") == "prose", span
        assert "Link to" not in span["text"]


def test_finding_to_judge_accepted_claims_do_not_cite_citation_fragments(monkeypatch):
    text = CITATION_FRONT_MATTER + "\n" + GENUINE_FACT
    result = _run_web(monkeypatch, text, _injury_proposition())
    data = result.artifact.structured_data
    prose_span_ids = {s["id"] for s in data["evidence_spans"] if s.get("quality") == "prose"}
    assert prose_span_ids

    requirement = UserRequirement(
        id="r_analysis",
        kind=RequirementKind.ANALYSIS,
        statement="find recent injury reports about Aaron Judge",
        value={"objective": "find recent injury reports about Aaron Judge"},
    )
    goal = Goal(statement="injury", raw_query="injury", requirements=[requirement])
    goal.propositions = build_propositions(goal)
    needs = [PlannerNeed(id="n_web", objective="research", capability="web_research", requires=[requirement.id])]
    projection = Judge().evaluate(goal, [result.artifact], needs)
    # Every accepted claim that references a span must reference a real prose span of the
    # fetched document, never an un-selected citation fragment.
    for claim in projection.claims:
        for ref in claim.support_refs:
            if ref.ref_type.value == "WEB_EVIDENCE":
                assert ref.ref_id in prose_span_ids or ref.ref_id.startswith("doc_")
