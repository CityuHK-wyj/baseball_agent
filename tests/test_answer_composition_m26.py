"""Generated answer-composition tests (M2.6).

The Composer must communicate every Judge-approved mandatory result, must not expand factual
authority beyond those results, and must not dump source metadata. Cases are generated from a
seed so any failure replays exactly with ``pytest -k "seed_<n>"``.
"""

from __future__ import annotations

import random

import pytest

from app.contracts.models import (
    AnswerProjection,
    AnswerProposition,
    Claim,
    ConsistencyStatus,
    CoverageState,
    Goal,
    GoalStatus,
    PropositionKind,
    Reference,
    ReferenceType,
    RequirementKind,
    SemanticInterpretation,
    UserRequirement,
    VerificationStatus,
)
from app.response.accepted_results import claim_factual_text, render_accepted_results
from app.response.answer_contract import build_answer_package
from app.response.composer import ResponseComposer
from app.verification.consistency import check_status_consistency, repair_answer

SEEDS = list(range(40))

ENTITIES = ["Aaron Judge", "Shohei Ohtani", "Mookie Betts", "Juan Soto", "Gerrit Cole"]
NUMBERS = [12, 25, 30, 41, 54, 60]
YEARS = [2019, 2021, 2024, 2025]
SOURCES = ["https://a.example/story", "https://b.example/story", "https://c.example/story"]


class _FakeModel:
    configured = True

    def __init__(self, answer="", fail=False):
        self._answer = answer
        self._fail = fail

    def complete_text(self, **kwargs):
        if self._fail:
            raise RuntimeError("model unavailable")
        return self._answer, 1, "fake"


def _goal(statement: str = "Who?", requirement_ids: list[str] | None = None) -> Goal:
    ids = requirement_ids or ["r1"]
    requirements = [
        UserRequirement(id=rid, kind=RequirementKind.ANALYSIS, statement=statement, value={"objective": statement})
        for rid in ids
    ]
    return Goal(statement=statement, raw_query=statement, requirements=requirements)


def _proposition(pid: str, requirement_id: str, statement: str, *, kind=PropositionKind.FACT,
                 mandatory=True, coverage=CoverageState.SATISFIED, claim_ids=None, finding_ids=None) -> AnswerProposition:
    return AnswerProposition(
        id=pid, requirement_id=requirement_id, statement=statement, kind=kind, mandatory=mandatory,
        coverage=coverage, supporting_claim_ids=list(claim_ids or []), evidence_finding_ids=list(finding_ids or []),
    )


def _web_claim(text: str, source: str, proposition_id: str, *, claim_id: str = "c1", span: str | None = None,
               title: str = "Story") -> Claim:
    return Claim(
        id=claim_id,
        text=f"{title} ({source}, retrieved 2026-09-29T00:00:00Z): {span or text}",
        support_refs=[Reference(ref_type=ReferenceType.WEB_EVIDENCE, ref_id=source, source=source, span=span or text)],
        epistemic=__import__("app.contracts.models", fromlist=["EpistemicStatus"]).EpistemicStatus.SUPPORTED,
        proposition_ids=[proposition_id],
        verification=VerificationStatus.SUPPORTED,
    )


def _artifact(span_text: str, source: str, *, context: str = "", span_id="sp1", finding_id="ef1", doc_id="doc1"):
    return type("A", (), {
        "structured_data": {
            "evidence_findings": [{"id": finding_id, "span_id": span_id, "document_id": doc_id,
                                   "source_url": source, "retrieved_at": "2026-09-29T00:00:00Z"}],
            "evidence_spans": [{"id": span_id, "document_id": doc_id, "text": span_text, "start": 0, "end": len(span_text)}],
            "documents": [{"id": doc_id, "url": source, "retrieved_at": "2026-09-29T00:00:00Z",
                           "body_text": context or span_text}],
        }
    })()


def _case(seed: int):
    rng = random.Random(seed)
    entity = rng.choice(ENTITIES)
    number = rng.choice(NUMBERS)
    year = rng.choice(YEARS)
    source = rng.choice(SOURCES)
    span = f"{entity} hit {number} home runs in {year}."
    proposition = _proposition("p1", "r1", f"{entity} home runs in {year}", claim_ids=["c1"], finding_ids=["ef1"])
    claim = _web_claim(span, source, "p1")
    goal = _goal()
    projection = AnswerProjection(
        goal_id=goal.id, goal_status=GoalStatus.COMPLETE, claims=[claim], propositions=[proposition],
    )
    return goal, projection, span, entity, number, source


# --------------------------------------------------------------------------- deterministic


@pytest.mark.parametrize("seed", SEEDS)
def test_mandatory_result_is_expressed_without_source_metadata(seed):
    goal, projection, span, _entity, _number, source = _case(seed)
    answer = ResponseComposer(_FakeModel(fail=True)).compose(goal, SemanticInterpretation(raw_query="Who?"), projection)
    assert span in answer
    assert "[S1]" in answer
    assert "http" not in answer and source not in answer
    assert check_status_consistency(goal, projection, answer).status == ConsistencyStatus.CONSISTENT


@pytest.mark.parametrize("seed", SEEDS)
def test_accepted_number_and_entity_are_preserved(seed):
    goal, projection, span, entity, number, _source = _case(seed)
    answer = ResponseComposer(_FakeModel(fail=True)).compose(goal, SemanticInterpretation(raw_query="Who?"), projection)
    assert str(number) in answer
    assert entity in answer


def test_negative_fact_stays_negative():
    text = "Shohei Ohtani did not pitch in 2024."
    proposition = _proposition("p1", "r1", "Shohei Ohtani pitching in 2024")
    claim = _web_claim(text, SOURCES[0], "p1")
    projection = AnswerProjection(goal_id="g", goal_status=GoalStatus.COMPLETE, claims=[claim], propositions=[proposition])
    answer = ResponseComposer(_FakeModel(fail=True)).compose(_goal(), SemanticInterpretation(raw_query="q"), projection)
    assert "did not pitch" in answer
    assert check_status_consistency(_goal(), projection, answer).status == ConsistencyStatus.CONSISTENT


def test_limited_goal_preserves_supported_partial_answer():
    proposition = _proposition("p1", "r1", "injury report", coverage=CoverageState.SATISFIED)
    unresolved = _proposition("p2", "r2", "trade report", mandatory=True, coverage=CoverageState.UNSUPPORTED)
    claim = _web_claim("Aaron Judge has a moderate calf injury.", SOURCES[0], "p1")
    goal = _goal(requirement_ids=["r1", "r2"])
    projection = AnswerProjection(
        goal_id=goal.id, goal_status=GoalStatus.LIMITED, claims=[claim],
        propositions=[proposition, unresolved],
        limitations=["not covered — ANALYSIS: trade report"],
    )
    answer = ResponseComposer(_FakeModel(fail=True)).compose(goal, SemanticInterpretation(raw_query="q"), projection)
    assert "calf injury" in answer
    assert "trade report" in answer
    assert "not covered" in answer


# --------------------------------------------------------------------------- authority boundary


def test_composer_context_does_not_expand_factual_boundary():
    approved = "Aaron Judge has a moderate calf injury."
    unapproved = "Separately, Aaron Judge once hit 99 home runs."
    proposition = _proposition("p1", "r1", "injury", finding_ids=["ef1"])
    claim = _web_claim(approved, SOURCES[0], "p1")
    goal = _goal()
    projection = AnswerProjection(goal_id="g", goal_status=GoalStatus.COMPLETE, claims=[claim], propositions=[proposition])
    artifact = _artifact(approved, SOURCES[0], context=f"{approved} {unapproved}")
    package = build_answer_package(goal, projection, [artifact])
    # The approved fact is the span; the surrounding context is present but is not authority.
    assert package.facts[0].text == approved
    assert "99" in package.facts[0].context[0].context_text
    assert "99" not in package.facts[0].text


def test_unapproved_number_in_synthesis_is_flagged_and_repaired():
    approved = "Aaron Judge has a moderate calf injury."
    proposition = _proposition("p1", "r1", "injury")
    claim = _web_claim(approved, SOURCES[0], "p1")
    goal = _goal()
    projection = AnswerProjection(goal_id="g", goal_status=GoalStatus.COMPLETE, claims=[claim], propositions=[proposition])
    # The model invents a number that is not in the approved fact.
    synthesized = "Aaron Judge has a moderate calf injury and will miss 42 games [S1]."
    answer = ResponseComposer(_FakeModel(synthesized)).compose(goal, SemanticInterpretation(raw_query="q"), projection)
    assert "未能在证据中找到直接来源" in answer or "42" not in answer.split("（注")[0]
    consistency = check_status_consistency(goal, projection, synthesized)
    assert consistency.status != ConsistencyStatus.CONSISTENT
    repaired = repair_answer(projection, synthesized)
    assert "42" not in repaired
    assert approved in repaired


def test_irrelevant_document_does_not_change_supported_content():
    approved = "Aaron Judge has a moderate calf injury."
    proposition = _proposition("p1", "r1", "injury", finding_ids=["ef1"])
    claim = _web_claim(approved, SOURCES[0], "p1")
    goal = _goal()
    projection = AnswerProjection(goal_id="g", goal_status=GoalStatus.COMPLETE, claims=[claim], propositions=[proposition])
    relevant = _artifact(approved, SOURCES[0], context=approved)
    with_noise = _artifact(approved, SOURCES[0], context=f"{approved} Gardening and flowers are unrelated.")
    a = build_answer_package(goal, projection, [relevant])
    b = build_answer_package(goal, projection, [with_noise])
    assert a.facts[0].text == b.facts[0].text == approved
    first = ResponseComposer(_FakeModel(fail=True)).compose(goal, SemanticInterpretation(raw_query="q"), projection, artifacts=[relevant])
    second = ResponseComposer(_FakeModel(fail=True)).compose(goal, SemanticInterpretation(raw_query="q"), projection, artifacts=[with_noise])
    assert first == second


# --------------------------------------------------------------------------- sources & language


@pytest.mark.parametrize("seed", SEEDS)
def test_source_ordering_does_not_change_factual_content(seed):
    goal, projection, span, _e, _n, source = _case(seed)
    other = _web_claim(span, SOURCES[(SOURCES.index(source) + 1) % len(SOURCES)], "p1", claim_id="c2")
    reordered = AnswerProjection(
        goal_id=projection.goal_id, goal_status=projection.goal_status, claims=[other, projection.claims[0]],
        propositions=projection.propositions,
    )
    first = render_accepted_results(projection)
    second = render_accepted_results(reordered)
    # The same fact and citation marker are present regardless of discovery order.
    assert span in first and span in second
    assert first.count("[S1]") == second.count("[S1]") == 1


def test_response_language_does_not_change_factual_content():
    approved = "Shohei Ohtani hit 54 home runs in 2024."
    proposition = _proposition("p1", "r1", "home runs")
    claim = _web_claim(approved, SOURCES[0], "p1")
    goal = _goal()
    projection = AnswerProjection(goal_id="g", goal_status=GoalStatus.COMPLETE, claims=[claim], propositions=[proposition])
    english = ResponseComposer(_FakeModel(fail=True)).compose(goal, SemanticInterpretation(raw_query="q", language="en"), projection)
    chinese = ResponseComposer(_FakeModel(fail=True)).compose(goal, SemanticInterpretation(raw_query="q", language="zh"), projection)
    # The approved fact (entity, number, season) is language-independent; the deterministic
    # rendering preserves it in both. A translated sentence is accepted as cross-script.
    assert "54" in english and "54" in chinese
    assert "Shohei Ohtani" in english and "Shohei Ohtani" in chinese
    assert check_status_consistency(goal, projection, "大谷翔平在2024年击出54支本垒打。").status in {
        ConsistencyStatus.CONSISTENT, ConsistencyStatus.UNCERTAIN,
    }


# --------------------------------------------------------------------------- navigation contract


@pytest.mark.parametrize("seed", SEEDS)
def test_answer_package_is_a_navigation_map(seed):
    goal, projection, span, _e, _n, source = _case(seed)
    artifact = _artifact(span, source, context=span)
    package = build_answer_package(goal, projection, [artifact])
    assert package.goal_status == GoalStatus.COMPLETE.value
    assert [fact.marker for fact in package.facts] == ["S1"]
    assert package.facts[0].claim_id == "c1"
    assert package.facts[0].requirement_id == "r1"
    entry = next(r for r in package.requirements if r.requirement_id == "r1")
    assert entry.satisfied
    assert entry.evidence and entry.evidence[0].approved_text == span
    assert entry.evidence[0].source == source


def test_limited_goal_omitting_satisfied_fact_is_repaired():
    proposition = _proposition("p1", "r1", "injury", coverage=CoverageState.SATISFIED)
    unresolved = _proposition("p2", "r2", "trade", coverage=CoverageState.UNSUPPORTED)
    claim = _web_claim("Aaron Judge has a moderate calf injury.", SOURCES[0], "p1")
    goal = _goal(requirement_ids=["r1", "r2"])
    projection = AnswerProjection(
        goal_id="g", goal_status=GoalStatus.LIMITED, claims=[claim], propositions=[proposition, unresolved],
        limitations=["not covered — trade report"],
    )
    bad = "I could not establish the trade report."
    consistency = check_status_consistency(goal, projection, bad)
    assert consistency.status == ConsistencyStatus.UNCERTAIN
    fixed = repair_answer(projection, bad, consistency.retracted_proposition_ids)
    assert "calf injury" in fixed
    assert check_status_consistency(goal, projection, fixed).status == ConsistencyStatus.CONSISTENT


def test_unresolved_requirements_are_navigable_but_not_facts():
    proposition = _proposition("p1", "r1", "injury", coverage=CoverageState.SATISFIED)
    unresolved = _proposition("p2", "r2", "trade", coverage=CoverageState.UNSUPPORTED)
    claim = _web_claim("Aaron Judge has a moderate calf injury.", SOURCES[0], "p1")
    goal = _goal(requirement_ids=["r1", "r2"])
    projection = AnswerProjection(goal_id="g", goal_status=GoalStatus.LIMITED, claims=[claim], propositions=[proposition, unresolved])
    package = build_answer_package(goal, projection)
    assert [r.requirement_id for r in package.unresolved] == ["r2"]
    assert all(fact.requirement_id == "r1" for fact in package.facts)


def test_claim_factual_text_prefers_span_over_source_preamble():
    claim = _web_claim("Aaron Judge has a moderate calf injury.", SOURCES[0], "p1")
    assert claim_factual_text(claim) == "Aaron Judge has a moderate calf injury."
    assert "http" not in claim_factual_text(claim)
