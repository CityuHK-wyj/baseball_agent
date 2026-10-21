"""Generated / property-based tests for Requirement -> AnswerProposition decomposition.

These tests do not depend on the named dogfood tasks. A seeded generator produces varied
entities, years, event/relation wordings and document structures, and each case is checked
against architectural properties (paraphrase invariance, atomicity, scope separation, offset
replay, no invented values). Every case is reproducible from its seed, so a failure can be
replayed exactly with ``pytest tests/test_generated_propositions.py -k "seed_<n>"``.
"""

from __future__ import annotations

import json
import random

import pytest

from tools.requirement_ancestry_fixture import Goal
from app.contracts.models import (
    AnalysisRequest,
    Constraint,
    EntityMention,
    EntityReference,
    EvidenceSource,
    EvidenceSpan,
    PropositionKind,
    RelationKind,
    RetrievedDocument,
    SemanticInterpretation,
    VerificationStatus,
)
from app.semantic.goals import RequirementBuilder
from app.verification.evidence import verify_span
from app.verification.propositions import build_propositions, required_tokens
from app.web.span_selector import select_span_dicts

SEEDS = list(range(40))

ENTITIES = [
    "Shohei Ohtani", "Aaron Judge", "Mookie Betts", "Gerrit Cole",
    "Ronald Acuna Jr.", "Freddie Freeman", "Juan Soto", "Corbin Burnes",
]
YEARS = [1994, 2001, 2016, 2021, 2024, 2025]
EVENTS = [
    "labor dispute", "contract negotiation", "knee injury", "trade",
    "rule change", "playoff run", "hitting adjustment", "roster move",
]

# Two paraphrases per relation, both of which must map to the same canonical label.
PARAPHRASES: dict[RelationKind, tuple[str, str]] = {
    RelationKind.CAUSE: ("causes of the {year} {event}", "reason for the {year} {event}"),
    RelationKind.EFFECT: ("effects of the {year} {event}", "impact of the {year} {event}"),
    RelationKind.COMPARISON: ("change in the {event}", "difference in the {event}"),
    RelationKind.QUOTE: ("interview quotes about the {event}", "podcast conversation about the {event}"),
    RelationKind.EXPLANATION: ("background of the {year} {event}", "origin of the {year} {event}"),
}

_FACT_KINDS = {
    PropositionKind.EXPLANATION,
    PropositionKind.FACT,
    PropositionKind.COMPARISON,
    PropositionKind.GROUNDED,
    PropositionKind.EVENT,
}


def _case(seed: int, relation: RelationKind, paraphrase: int = 0) -> tuple[str, str, str, int]:
    rng = random.Random(seed)
    entity = rng.choice(ENTITIES)
    year = rng.choice(YEARS)
    event = rng.choice(EVENTS)
    wording = PARAPHRASES[relation][paraphrase]
    return entity, wording.format(year=year, event=event), event, year


def _build(query: str, analysis: str, entity: str, year: int) -> tuple[Goal, list]:
    semantic = SemanticInterpretation(
        raw_query=query,
        language="en",
        entities=[EntityMention(mention=entity, type_hint="player")],
        entity_references=[EntityReference(surface=entity, type_hint="player")],
        analysis_requests=[AnalysisRequest(text=analysis, objective=analysis)],
        constraints=[Constraint(kind="season", value=year)],
    )
    goal = RequirementBuilder().build(semantic)
    return goal, build_propositions(goal)


def _analysis_propositions(goal: Goal, propositions: list) -> list:
    analysis_ids = {r.id for r in goal.requirements if r.kind.value == "ANALYSIS"}
    return [p for p in propositions if p.requirement_id in analysis_ids]


def _relation_labels(propositions: list) -> list[str]:
    labels = []
    for proposition in propositions:
        if proposition.relation_semantics is None:
            continue
        labels.append(proposition.relation_semantics.relation_kind.value)
    return sorted(labels)


def _serialize(propositions: list) -> str:
    return json.dumps(
        [
            {
                "kind": p.kind.value,
                "statement": p.statement,
                "required_information": p.required_information,
                "subject": p.subject,
                "relation": (p.relation_semantics.relation_kind.value if p.relation_semantics else None),
            }
            for p in propositions
        ],
        sort_keys=True,
        ensure_ascii=False,
    )


# --------------------------------------------------------------------------- generators


def test_seeded_generation_is_reproducible():
    for seed in SEEDS:
        entity, analysis, _event, year = _case(seed, RelationKind.CAUSE)
        _goal_a, props_a = _build(analysis + "?", analysis, entity, year)
        _goal_b, props_b = _build(analysis + "?", analysis, entity, year)
        assert _serialize(props_a) == _serialize(props_b), f"seed {seed} not reproducible"


@pytest.mark.parametrize("seed", SEEDS)
def test_paraphrase_preserves_relation(seed):
    relation = list(PARAPHRASES)[seed % len(PARAPHRASES)]
    entity, analysis_a, _event, year = _case(seed, relation, 0)
    _entity_b, analysis_b, _event_b, _year_b = _case(seed, relation, 1)
    _goal_a, props_a = _build(analysis_a + "?", analysis_a, entity, year)
    _goal_b, props_b = _build(analysis_b + "?", analysis_b, entity, year)
    labels_a = _relation_labels(_analysis_propositions(_goal_a, props_a))
    labels_b = _relation_labels(_analysis_propositions(_goal_b, props_b))
    assert labels_a, f"seed {seed}: no relation label for {relation}"
    assert set(labels_a) & set(labels_b), f"seed {seed}: paraphrases disagreed {labels_a} vs {labels_b}"


@pytest.mark.parametrize("seed", SEEDS)
def test_decomposition_is_atomic_and_non_empty(seed):
    relation = list(PARAPHRASES)[seed % len(PARAPHRASES)]
    entity, analysis, _event, year = _case(seed, relation)
    goal, propositions = _build(analysis + "?", analysis, entity, year)
    analysis_props = _analysis_propositions(goal, propositions)
    assert analysis_props, f"seed {seed}: no analysis proposition"
    for proposition in analysis_props:
        assert proposition.required_information.strip(), f"seed {seed}: empty required information"
        if proposition.relation_semantics is not None:
            assert len(proposition.relation_semantics.relation_kinds) <= 1, (
                f"seed {seed}: non-atomic multi-label proposition {proposition.relation_semantics.relation_kinds}"
            )
        assert proposition.subject, f"seed {seed}: proposition lost its subject"


@pytest.mark.parametrize("seed", SEEDS)
def test_required_information_excludes_subject(seed):
    """The subject is a separate slot; it must not inflate the required-token match."""
    relation = list(PARAPHRASES)[seed % len(PARAPHRASES)]
    entity, analysis, _event, year = _case(seed, relation)
    goal, propositions = _build(analysis + "?", analysis, entity, year)
    for proposition in _analysis_propositions(goal, propositions):
        if proposition.subject:
            assert proposition.subject.lower() not in proposition.required_information.lower(), (
                f"seed {seed}: subject leaked into required information {proposition.required_information!r}"
            )


@pytest.mark.parametrize("seed", SEEDS)
def test_proposition_does_not_invent_values(seed):
    relation = list(PARAPHRASES)[seed % len(PARAPHRASES)]
    entity, analysis, _event, year = _case(seed, relation)
    goal, propositions = _build(analysis + "?", analysis, entity, year)
    source_numbers = set(json.dumps({"a": analysis, "q": analysis}))
    source_digits = set(analysis)
    for proposition in _analysis_propositions(goal, propositions):
        text = f"{proposition.statement} {proposition.required_information}"
        invented = {ch for ch in text if ch.isdigit()} - source_digits
        assert not invented, f"seed {seed}: invented digits {invented} in {text!r}"


@pytest.mark.parametrize("seed", SEEDS)
def test_harmless_wording_change_is_stable(seed):
    relation = list(PARAPHRASES)[seed % len(PARAPHRASES)]
    entity, analysis, _event, year = _case(seed, relation)
    _goal_a, props_a = _build(analysis + "?", analysis, entity, year)
    _goal_b, props_b = _build("Could you please " + analysis + "? Thank you.", analysis, entity, year)
    assert _serialize(props_a) == _serialize(props_b), f"seed {seed}: harmless wording changed propositions"


@pytest.mark.parametrize("seed", SEEDS)
def test_entity_substitution_changes_subject_not_relation(seed):
    relation = list(PARAPHRASES)[seed % len(PARAPHRASES)]
    entity, analysis, _event, year = _case(seed, relation)
    other = next(name for name in ENTITIES if name != entity)
    goal_a, props_a = _build(analysis + "?", analysis, entity, year)
    goal_b, props_b = _build(analysis.replace(entity, other) + "?", analysis, other, year)
    labels_a = _relation_labels(_analysis_propositions(goal_a, props_a))
    labels_b = _relation_labels(_analysis_propositions(goal_b, props_b))
    assert labels_a == labels_b, f"seed {seed}: entity substitution changed the relation"
    subjects_a = {p.subject for p in _analysis_propositions(goal_a, props_a)}
    subjects_b = {p.subject for p in _analysis_propositions(goal_b, props_b)}
    assert subjects_a != subjects_b, f"seed {seed}: entity substitution did not change the subject"


@pytest.mark.parametrize("seed", SEEDS)
def test_scope_mismatch_is_not_supported(seed):
    relation = list(PARAPHRASES)[seed % len(PARAPHRASES)]
    entity, analysis, _event, year = _case(seed, relation)
    goal, propositions = _build(analysis + "?", analysis, entity, year)
    analysis_props = _analysis_propositions(goal, propositions)
    if not analysis_props:
        pytest.skip("no analysis proposition")
    proposition = next((p for p in analysis_props if p.time_scope.get("season")), analysis_props[0])
    wrong_year = int(year) - 3
    # Keep the relation wording but remove the requested year, then inject a different year.
    analysis_without_year = analysis.replace(str(year), "").strip()
    body = f"{entity} {analysis_without_year} in {wrong_year}, according to reports."
    finding = _verify(proposition, body)
    # A different year must never yield SUPPORTED; it is either a MISMATCH or UNKNOWN.
    assert finding.status != VerificationStatus.SUPPORTED, f"seed {seed}: wrong-year passage supported"
    assert finding.checks["time_state"] in {"MISMATCH", "UNKNOWN"}


@pytest.mark.parametrize("seed", SEEDS)
def test_irrelevant_entity_is_irrelevant(seed):
    relation = list(PARAPHRASES)[seed % len(PARAPHRASES)]
    entity, analysis, _event, year = _case(seed, relation)
    goal, propositions = _build(analysis + "?", analysis, entity, year)
    analysis_props = _analysis_propositions(goal, propositions)
    if not analysis_props:
        pytest.skip("no analysis proposition")
    proposition = next((p for p in analysis_props if p.subject), analysis_props[0])
    other = next(name for name in ENTITIES if name not in proposition.subject)
    body = f"{other} was involved in a major {analysis} during {year}, according to reports."
    finding = _verify(proposition, body)
    if proposition.subject:
        assert finding.status != VerificationStatus.SUPPORTED, f"seed {seed}: unrelated entity supported"


@pytest.mark.parametrize("seed", SEEDS)
def test_offsets_remain_replayable(seed):
    rng = random.Random(seed ^ 0x5EED)
    entity, analysis, _event, year = _case(seed, RelationKind.CAUSE)
    _goal, propositions = _build(analysis + "?", analysis, entity, year)
    filler = " ".join(rng.choice(["The weather was mild.", "Fans filled the stands.", "Reporters waited."]) for _ in range(rng.randint(0, 6)))
    body = f"{filler} {entity} caused a major {analysis} in {year}. The result was widely reported."
    spans = select_span_dicts(body, propositions=[p.model_dump(exclude={"coverage"}) for p in propositions])
    assert spans, f"seed {seed}: no spans selected"
    for span in spans:
        assert body[span["start"] : span["end"]] == span["text"], f"seed {seed}: offset replay broken"
        assert span["source_type"] == "DOCUMENT_BODY"


def test_compound_cause_effect_does_not_collapse():
    """A compound obligation becomes independently testable relation propositions."""
    from app.contracts.models import RelationSemantics, RequirementCompletion, RequirementKind, UserRequirement
    from app.semantic.relations import build_relation_semantics

    statement = "causes and effects of the 1994 Major League Baseball strike"
    requirement = UserRequirement(
        id="r_compound",
        kind=RequirementKind.ANALYSIS,
        statement=statement,
        value={"objective": statement},
        completion=RequirementCompletion.EXPLANATION,
        relation_semantics=build_relation_semantics(text=statement),
    )
    entity = UserRequirement(
        id="r_entity", kind=RequirementKind.ENTITY, statement="identify MLB",
        value={"mention": "Major League Baseball"},
    )
    goal = Goal(statement=statement, raw_query=statement, requirements=[entity, requirement])
    propositions = build_propositions(goal)
    analysis = [p for p in propositions if p.requirement_id == "r_compound"]
    labels = {p.relation_semantics.relation_kind for p in analysis if p.relation_semantics}
    assert {RelationKind.CAUSE, RelationKind.EFFECT} <= labels, labels
    assert len(analysis) == 2
    # Independent identity: supporting one cannot be mistaken for the other.
    assert len({p.id for p in analysis}) == 2


# --------------------------------------------------------------------------- LLM path


class _FakeLLM:
    configured = True

    def __init__(self, propositions):
        self._propositions = propositions
        self.calls = 0

    def complete_json(self, *, role, system, user, max_tokens):  # noqa: A002 - test double
        self.calls += 1

        class _Result:
            def __init__(self, data):
                self.data = data

        return _Result({"propositions": self._propositions})


def _compound_goal() -> Goal:
    from app.contracts.models import RequirementCompletion, RequirementKind, UserRequirement
    from app.semantic.relations import build_relation_semantics

    statement = "causes and effects of the 1994 Major League Baseball strike"
    requirement = UserRequirement(
        id="r_compound",
        kind=RequirementKind.ANALYSIS,
        statement=statement,
        value={"objective": statement},
        completion=RequirementCompletion.EXPLANATION,
        relation_semantics=build_relation_semantics(text=statement),
    )
    entity = UserRequirement(
        id="r_entity", kind=RequirementKind.ENTITY, statement="identify MLB",
        value={"mention": "Major League Baseball"},
    )
    return Goal(statement=statement, raw_query=statement, requirements=[entity, requirement])


def test_llm_decomposition_is_used_when_valid():
    llm = _FakeLLM([
        {"statement": "owners proposed a salary cap", "kind": "EXPLANATION", "subject": "Major League Baseball",
         "required_information": "owners proposed a salary cap", "relation": "CAUSE"},
        {"statement": "the postseason was cancelled", "kind": "EXPLANATION", "subject": "Major League Baseball",
         "required_information": "the postseason was cancelled", "relation": "EFFECT"},
    ])
    propositions = build_propositions(_compound_goal(), llm=llm)
    analysis = [p for p in propositions if p.requirement_id == "r_compound"]
    assert llm.calls == 1
    assert {p.required_information for p in analysis} == {"owners proposed a salary cap", "the postseason was cancelled"}


def test_llm_invented_number_is_rejected_and_falls_back():
    llm = _FakeLLM([
        {"statement": "owners proposed a 17 team league", "kind": "EXPLANATION", "subject": "Major League Baseball",
         "required_information": "owners proposed a 17 team league", "relation": "CAUSE"},
    ])
    propositions = build_propositions(_compound_goal(), llm=llm)
    analysis = [p for p in propositions if p.requirement_id == "r_compound"]
    # The invented value is rejected; the deterministic CAUSE/EFFECT split is used instead.
    assert {p.relation_semantics.relation_kind for p in analysis} == {RelationKind.CAUSE, RelationKind.EFFECT}
    assert all("17" not in p.required_information for p in analysis)


def test_llm_invented_subject_is_rejected_and_falls_back():
    llm = _FakeLLM([
        {"statement": "some other league proposed a cap", "kind": "EXPLANATION", "subject": "Invented League",
         "required_information": "some other league proposed a cap", "relation": "CAUSE"},
    ])
    propositions = build_propositions(_compound_goal(), llm=llm)
    analysis = [p for p in propositions if p.requirement_id == "r_compound"]
    assert {p.relation_semantics.relation_kind for p in analysis} == {RelationKind.CAUSE, RelationKind.EFFECT}
    assert all(p.subject == "Major League Baseball" for p in analysis)


def test_adding_irrelevant_text_does_not_change_verification():
    statement = "causes of the 1994 Major League Baseball strike"
    goal, propositions = _build(
        "What caused the 1994 Major League Baseball strike?",
        statement, "Major League Baseball", 1994,
    )
    analysis = _analysis_propositions(goal, propositions)
    proposition = next(p for p in analysis if p.time_scope.get("season"))
    target = "In 1994, Major League Baseball owners proposed a salary cap in response to financial losses."
    base = _verify(proposition, target)
    padded = ("Unrelated gardening notes about flowers and trees. " * 5) + target
    # Same target span text: the decision must be identical regardless of surrounding text.
    padded_finding = _verify(proposition, target, document_body=padded)
    assert base.status == padded_finding.status
    assert base.checks["matched_tokens"] == padded_finding.checks["matched_tokens"]


# --------------------------------------------------------------------------- helpers


def _verify(proposition, target: str, document_body: str | None = None) -> object:
    body = document_body if document_body is not None else target
    document = RetrievedDocument(
        id="doc_gen",
        url="https://example.com/gen",
        retrieved_at="2026-01-01T00:00:00Z",
        content_hash="hash-gen",
        body_text=body,
        retrieval_status="OK",
    )
    start = body.index(target) if target in body else 0
    span = EvidenceSpan(
        document_id="doc_gen",
        text=target,
        start=start,
        end=start + len(target),
        source_type=EvidenceSource.DOCUMENT_BODY,
        retrieval_status="OK",
    )
    return verify_span(proposition, span, document, llm=None, allow_llm=False)
