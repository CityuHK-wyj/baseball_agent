"""WSA-06 — Composer-only inference boundary.

The Result Composer is the only layer allowed to synthesise / infer. These tests exercise the
*response policy*: facts stay sourced, qualified evidence keeps its limitation, model synthesis
is clearly the model's own analysis, citations do not falsely imply that sources prove an
inference, and no numeric fact is invented.

Open-world classification (is a statement factual, causal, framed?) is owned by the bounded LLM
response-conformance assessor. The deterministic layer only validates the typed state and
applies repair. The cue-based safety tripwire can only fail closed; it never certifies safety.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from app.contracts.models import (
    AnswerProjection,
    CoverageState,
    GoalStatus,
    QualifiedEvidence,
    SemanticInterpretation,
)
from app.response.answer_contract import AnswerFact, AnswerPackage
from app.response.composer import ResponseComposer
from app.response.composition_contract import (
    parse_composer_draft,
    parse_conformance_assessment,
)
from app.verification.composition_conformance import (
    conform_composition,
    request_conformance_assessment,
)
from tests.test_answer_composition_m26 import _goal, _proposition, _web_claim
from tests.conformance_fixtures import bound_review, complete_review_data


# --------------------------------------------------------------------------- fixtures


def _pkg(facts=(), qualified=(), limitations=(), unresolved=(), status="COMPLETE") -> AnswerPackage:
    return AnswerPackage(
        goal_id="g", goal_status=status, language="en",
        facts=[AnswerFact(marker=f"S{i}", text=text, claim_id=f"c{i}") for i, text in enumerate(facts, 1)],
        qualified_evidence=list(qualified),
        limitations=list(limitations),
        unresolved=list(unresolved),
    )


def _qual(text: str, limitation: str, *, kind: str = "AUTHORITY", qid: str = "q1") -> QualifiedEvidence:
    return QualifiedEvidence(id=qid, span_text=text, limitation=limitation, kind=kind)


def _stmt(kind: str, text: str, citations=None) -> dict:
    return {"kind": kind, "text": text, "citations": list(citations or [])}


def _verdict(index: int, **kwargs) -> dict:
    base = {
        "index": index, "epistemic": "ANALYSIS", "approved_claim_backed": False,
        "material_conclusion": False, "framed_as_interpretation": False,
        "citations_on_inference": False, "limitation_retained": True,
    }
    base.update(kwargs)
    return base


def _conform(statements, verdicts, package: AnswerPackage):
    result = parse_composer_draft({"statements": statements})
    assessment = bound_review({"statements": verdicts}, package, result) if verdicts is not None else None
    return conform_composition(result, package, assessment, language="en")


def _unsafe_kinds(result) -> set[str]:
    return {
        violation.kind for violation in result.violations
        if violation.kind in {
            "UNFRAMED_ANALYSIS", "CITATION_OVERCLAIM", "UNSUPPORTED_CAUSAL",
            "QUALIFIED_AS_FACT", "QUALIFIED_CONFIRMATION", "UNSOURCED_FACTUAL",
            "UNSOURCED_QUALIFIED", "UNSUPPORTED_FACTUAL",
        }
    }


def _causal_factual_lines(text: str) -> list[str]:
    """Lines that present a causal claim as a sourced fact (with a citation marker)."""
    cues = ("caused", "led to", "resulted in", "because of", "due to", "the reason")
    out = []
    for line in text.splitlines():
        low = line.lower()
        if line.strip().startswith("my analysis"):
            continue
        if any(cue in low for cue in cues) and re.search(r"\[S\d+\]", line):
            out.append(line)
    return out


class _ComposerFixture:
    """A composer + conformance-assessor double; dispatches on the system prompt."""

    configured = True

    def __init__(self, composition: dict, assessment: dict | None = None):
        self.composition = composition
        self.assessment = assessment
        self.systems: list[str] = []

    def complete_json(self, *, role="response", system="", user="", **kwargs):
        self.systems.append(system)
        if "response-conformance assessor" in system:
            data = complete_review_data(self.assessment, parse_composer_draft(self.composition)) if self.assessment is not None else {"statements": []}
        else:
            data = self.composition
        return SimpleNamespace(data=data, model="fixture", latency_ms=1)


# =========================================================================== adversarial


def test_approved_a_plus_approved_b_does_not_become_factual_causality():
    package = _pkg(facts=[
        "Player X missed 12 games during the 2025 stretch.",
        "The team OPS against left-handed pitching fell 65 points over the same period.",
    ])
    result = _conform(
        [_stmt("FACTUAL", "Player X's absence caused the team's decline against left-handed pitching.", ["S1", "S2"])],
        [_verdict(0, epistemic="ANALYSIS", material_conclusion=True, framed_as_interpretation=False,
                  citations_on_inference=True)],
        package,
    )
    assert _causal_factual_lines(result.text) == []
    assert "my analysis" in result.text.lower()
    # Premises are still stated factually and cited next to the fact they support.
    assert "missed 12 games" in result.text and "[S1]" in result.text
    assert "fell 65 points" in result.text and "[S2]" in result.text
    assert "UNSUPPORTED_CAUSAL" in {v.kind for v in result.violations} or \
           "CITATION_OVERCLAIM" in {v.kind for v in result.violations} or \
           "UNFRAMED_ANALYSIS" in {v.kind for v in result.violations}


def test_two_unrelated_facts_do_not_invent_a_relationship_as_fact():
    package = _pkg(facts=[
        "Shohei Ohtani hit 54 home runs in 2024.",
        "The Dodgers won 98 games in 2024.",
    ])
    result = _conform(
        [_stmt("ANALYSIS", "Ohtani's home runs were the reason the Dodgers won 98 games.", ["S1", "S2"])],
        [_verdict(0, material_conclusion=True, framed_as_interpretation=False)],
        package,
    )
    assert _causal_factual_lines(result.text) == []
    assert "my analysis" in result.text.lower()
    assert "54 home runs" in result.text and "98 games" in result.text


def test_approved_plus_qualified_does_not_become_overconfident_synthesis():
    package = _pkg(
        facts=["The player's performance declined in the second half."],
        qualified=[_qual("A community report says the player may still be dealing with discomfort.",
                         "the report is not fully verified", kind="ASSERTION")],
    )
    result = _conform(
        [
            _stmt("FACTUAL", "His lingering injury caused the decline.", []),
            _stmt("ANALYSIS", "Taken together, this suggests the injury may be a contributing factor.", ["S1", "Q1"]),
        ],
        [_verdict(0, epistemic="ANALYSIS", material_conclusion=True, framed_as_interpretation=False),
         _verdict(1, epistemic="ANALYSIS", material_conclusion=True, framed_as_interpretation=True)],
        package,
    )
    assert _causal_factual_lines(result.text) == []
    assert "my analysis" in result.text.lower()
    # The qualified item keeps its limitation; it is not laundered into a fact.
    assert "not fully verified" in result.text
    assert "community report" in result.text.lower()


def test_qualified_evidence_rewritten_as_fact_is_repaired():
    package = _pkg(
        facts=["The player appeared in 120 games."],
        qualified=[_qual("A reporter says the player may still be injured.",
                         "the report is unverified", kind="ASSERTION")],
    )
    result = _conform(
        [_stmt("FACTUAL", "The player is still injured.", [])],
        [_verdict(0, epistemic="QUALIFIED", approved_claim_backed=False, limitation_retained=False)],
        package,
    )
    assert "unverified" in result.text
    assert "not established" in result.text or "unverified" in result.text


def test_speculation_rewritten_as_confirmation_keeps_limitation():
    package = _pkg(
        facts=["The team announced a roster move."],
        qualified=[_qual("A community post speculates the player could be traded.",
                         "speculative community report, not confirmed", kind="ASSERTION")],
    )
    result = _conform(
        [_stmt("QUALIFIED", "The player is being traded.", ["Q1"])],
        [_verdict(0, epistemic="QUALIFIED", limitation_retained=False)],
        package,
    )
    assert "not confirmed" in result.text
    assert "not established" in result.text or "speculative" in result.text


def test_stale_qualified_evidence_is_not_rewritten_as_current():
    package = _pkg(
        facts=["The player appeared in 120 games."],
        qualified=[_qual("A 2019 article says the player was injured at that time.",
                         "the source was published in 2019 and may not describe the current state",
                         kind="TEMPORAL")],
    )
    result = _conform(
        [_stmt("QUALIFIED", "The player is currently injured.", ["Q1"])],
        [_verdict(0, epistemic="QUALIFIED", limitation_retained=False)],
        package,
    )
    assert "published in 2019" in result.text
    assert "not established" in result.text


def test_partial_comparison_invented_comparative_number_is_removed():
    package = _pkg(
        facts=["His 2025 AVG was .312."],
        qualified=[_qual("2025 AVG = .312", "both operands are required for the comparison",
                         kind="PARTIAL_OPERAND")],
        limitations=["2026 AVG was not established"],
        unresolved=[_unresolved("comparison")],
    )
    result = _conform(
        [_stmt("ANALYSIS", "His AVG improved from .312 to .330.", ["S1", "Q1"])],
        [_verdict(0, material_conclusion=False)],
        package,
    )
    assert ".330" not in result.text
    assert ".312" in result.text


def test_invented_numeric_fact_in_synthesis_is_removed():
    package = _pkg(facts=["The player hit 30 home runs in 2024."])
    result = _conform(
        [_stmt("ANALYSIS", "He also stole 25 bases and drove in 100 runs.", ["S1"])],
        [_verdict(0, material_conclusion=False)],
        package,
    )
    assert "25" not in result.text
    assert "100" not in result.text
    assert "30 home runs" in result.text


def test_citations_attached_to_inference_are_stripped():
    package = _pkg(facts=["The player's OPS fell by 60 points.", "The player missed 20 games."])
    result = _conform(
        [_stmt("ANALYSIS", "His absence led to the OPS drop. [S1][S2]", [])],
        [_verdict(0, epistemic="ANALYSIS", material_conclusion=True, framed_as_interpretation=False,
                  citations_on_inference=True)],
        package,
    )
    analysis_line = next(line for line in result.text.splitlines() if "led to" in line.lower())
    assert "[S1]" not in analysis_line and "[S2]" not in analysis_line
    assert "my analysis" in analysis_line.lower()
    # The premises remain cited factually.
    assert "OPS fell by 60 points" in result.text and "[S1]" in result.text


def test_conflicting_evidence_is_not_silently_resolved_by_the_composer():
    package = _pkg(
        facts=["One source reports the player is injured."],
        qualified=[_qual("Another source reports the player is not injured.",
                         "sources conflict; neither is authoritative",
                         kind="CONTRADICTED")],
    )
    result = _conform(
        [_stmt("ANALYSIS", "The evidence definitively shows he is injured.", ["S1"])],
        [_verdict(0, epistemic="ANALYSIS", material_conclusion=True, framed_as_interpretation=False)],
        package,
    )
    assert "not injured" in result.text
    assert "conflict" in result.text.lower()


def test_goal_limited_is_not_presented_as_fully_resolved():
    package = _pkg(
        facts=["His 2025 AVG was .312."],
        limitations=["His 2026 AVG could not be verified"],
        unresolved=[_unresolved("2026 AVG")],
        status="LIMITED",
    )
    result = _conform(
        [_stmt("FACTUAL", "His 2025 AVG was .312.", ["S1"])],
        None,
        package,
    )
    assert ".312" in result.text
    assert "could not be verified" in result.text


# =========================================================================== tripwire semantics


def test_tripwire_forces_framing_even_when_assessor_clears_a_causal_statement():
    """A positive safety cue may only fail closed; it cannot be overridden toward unsafety."""
    package = _pkg(facts=["Player X missed 12 games.", "The team's OPS fell by 65 points."])
    result = _conform(
        [_stmt("ANALYSIS", "Player X's absence caused the decline.", ["S1", "S2"])],
        [_verdict(0, material_conclusion=False, framed_as_interpretation=False)],
        package,
    )
    assert "my analysis" in result.text.lower()
    assert "SAFETY" in {v.kind for v in result.violations} or \
           "UNFRAMED_ANALYSIS" in {v.kind for v in result.violations}


def test_tentative_wording_alone_does_not_certify_an_inference():
    """Presence of 'may' does not make a material, unframed conclusion acceptable."""
    package = _pkg(facts=["Player X missed 12 games.", "The team's OPS fell by 65 points."])
    result = _conform(
        [_stmt("ANALYSIS", "The injury may have caused the decline.", ["S1", "S2"])],
        [_verdict(0, material_conclusion=True, framed_as_interpretation=False)],
        package,
    )
    assert "my analysis" in result.text.lower()
    assert "UNFRAMED_ANALYSIS" in {v.kind for v in result.violations}


def test_typed_analysis_without_assessor_omits_free_prose_regardless_of_causal_wording():
    package = _pkg(facts=["Player X missed 12 games.", "The team's OPS fell by 65 points."])
    result = _conform(
        [_stmt("FACTUAL", "Player X's absence caused the decline.", ["S1", "S2"])],
        None,
        package,
    )
    assert _causal_factual_lines(result.text) == []
    assert "absence caused" not in result.text.lower()
    assert "MISSING_SEMANTIC_CONFORMANCE" in {v.kind for v in result.violations}


# =========================================================================== utility


def test_useful_descriptive_synthesis_is_not_blocked():
    package = _pkg(facts=["AVG .312.", "OBP .390.", "SLG .570."])
    result = _conform(
        [_stmt("ANALYSIS", "Overall, he had a strong offensive season.", ["S1", "S2", "S3"])],
        [_verdict(0, material_conclusion=False)],
        package,
    )
    assert "strong offensive season" in result.text
    assert not _unsafe_kinds(result)


def test_reasonable_trend_interpretation_is_retained():
    package = _pkg(facts=["His strikeout rate increased.", "His barrel rate decreased."])
    result = _conform(
        [_stmt("ANALYSIS", "Taken together, his offensive quality declined during this stretch.", ["S1", "S2"])],
        [_verdict(0, material_conclusion=True, framed_as_interpretation=True)],
        package,
    )
    assert "offensive quality declined" in result.text
    assert not _unsafe_kinds(result)


def test_explicit_user_requested_judgment_is_retained():
    package = _pkg(facts=["Player A produced 4.1 WAR.", "Player B produced 2.3 WAR."])
    result = _conform(
        [_stmt("ANALYSIS", "Given the verified production, I would prefer Player A.", ["S1", "S2"])],
        [_verdict(0, material_conclusion=True, framed_as_interpretation=True)],
        package,
    )
    assert "I would prefer Player A" in result.text
    assert not _unsafe_kinds(result)


def test_plausible_explanation_with_proper_uncertainty_is_retained():
    package = _pkg(
        facts=["His performance declined."],
        qualified=[_qual("A reporter says he may still be dealing with discomfort.",
                         "the report is not fully verified", kind="ASSERTION")],
    )
    result = _conform(
        [_stmt("ANALYSIS", "One possible explanation is lingering discomfort, although the report is not fully verified.", ["S1", "Q1"])],
        [_verdict(0, material_conclusion=True, framed_as_interpretation=True)],
        package,
    )
    assert "lingering discomfort" in result.text
    assert "not fully verified" in result.text
    assert not _unsafe_kinds(result)


def test_approved_qualified_and_missing_in_one_natural_answer():
    package = _pkg(
        facts=["His 2025 AVG was .312."],
        qualified=[_qual("A community report says he was optioned to Triple-A.",
                         "community report, not verified", kind="AUTHORITY")],
        limitations=["His 2026 AVG could not be verified"],
        unresolved=[_unresolved("2026 AVG")],
        status="LIMITED",
    )
    result = _conform(
        [
            _stmt("FACTUAL", "His 2025 AVG was .312.", ["S1"]),
            _stmt("QUALIFIED", "A community report says he was optioned to Triple-A.", ["Q1"]),
            _stmt("LIMITATION", "I could not verify his 2026 AVG."),
        ],
        None,
        package,
    )
    assert ".312" in result.text
    assert "optioned to Triple-A" in result.text
    assert "not verified" in result.text or "could not be verified" in result.text
    assert "2026" in result.text


# ================================================================ composer integration


class _Projection:
    def __init__(self, projection):
        self._projection = projection


def _two_fact_projection():
    p1 = _proposition("p1", "r1", "missed games", claim_ids=["c1"])
    p2 = _proposition("p2", "r2", "team decline", claim_ids=["c2"])
    c1 = _web_claim("Player X missed 12 games.", "https://a.example/x", "p1", claim_id="c1")
    c2 = _web_claim("The team's OPS against left-handed pitching fell 65 points.",
                    "https://b.example/y", "p2", claim_id="c2")
    goal = _goal(requirement_ids=["r1", "r2"])
    projection = AnswerProjection(
        goal_id=goal.id, goal_status=GoalStatus.COMPLETE, claims=[c1, c2], propositions=[p1, p2],
    )
    return goal, projection


def test_composer_integration_frames_causal_inference_and_keeps_premises_cited():
    goal, projection = _two_fact_projection()
    composition = {"statements": [
        _stmt("FACTUAL", "Player X's absence caused the team's decline.", ["S1", "S2"]),
    ]}
    assessment = {"statements": [
        _verdict(0, epistemic="ANALYSIS", material_conclusion=True, framed_as_interpretation=False,
                 citations_on_inference=True),
    ]}
    llm = _ComposerFixture(composition, assessment)
    answer = ResponseComposer(llm).compose(goal, SemanticInterpretation(raw_query="why?"), projection)
    assert _causal_factual_lines(answer) == []
    assert "my analysis" in answer.lower()
    assert "missed 12 games" in answer and "[S1]" in answer
    assert "fell 65 points" in answer and "[S2]" in answer
    # Both the composer and the bounded conformance assessor were consulted.
    assert any("response composer" in system for system in llm.systems)
    assert any("response-conformance assessor" in system for system in llm.systems)


def test_composer_integration_retains_valid_analysis_and_qualified_limitation():
    prop = _proposition("p1", "r1", "performance", claim_ids=["c1"])
    claim = _web_claim("His performance declined in the second half.", "https://a.example/p", "p1", claim_id="c1")
    goal = _goal()
    projection = AnswerProjection(
        goal_id=goal.id, goal_status=GoalStatus.LIMITED, claims=[claim], propositions=[prop],
        qualified_evidence=[_qual("A reporter says he may still be dealing with discomfort.",
                                  "the report is not fully verified", kind="ASSERTION")],
        limitations=["an official injury status was not obtained"],
    )
    composition = {"statements": [
        _stmt("FACTUAL", "His performance declined in the second half.", ["S1"]),
        _stmt("QUALIFIED", "A reporter says he may still be dealing with discomfort.", ["Q1"]),
        _stmt("ANALYSIS", "Taken together, this suggests lingering discomfort may be one factor.", ["S1", "Q1"]),
    ]}
    assessment = {"statements": [
        _verdict(0, epistemic="FACTUAL", approved_claim_backed=True),
        _verdict(1, epistemic="QUALIFIED", limitation_retained=True),
        _verdict(2, epistemic="ANALYSIS", material_conclusion=True, framed_as_interpretation=True),
    ]}
    answer = ResponseComposer(_ComposerFixture(composition, assessment)).compose(
        goal, SemanticInterpretation(raw_query="what happened?"), projection)
    assert "performance declined" in answer
    assert "lingering discomfort" in answer
    assert "not fully verified" in answer
    assert "one factor" in answer


def test_composer_integration_untyped_answer_without_assessor_falls_back_safely():
    goal, projection = _two_fact_projection()
    # A text-only model (no JSON) and no conformance assessor: free prose is not trusted.
    class _TextOnly:
        configured = True

        def complete_text(self, **kwargs):
            return "Player X's absence caused the team's decline. [S1][S2]", 1, "text"

    answer = ResponseComposer(_TextOnly()).compose(goal, SemanticInterpretation(raw_query="why?"), projection)
    assert _causal_factual_lines(answer) == []
    assert "missed 12 games" in answer
    assert "fell 65 points" in answer


def test_composer_integration_tripwire_fails_closed_without_assessor():
    goal, projection = _two_fact_projection()
    composition = {"statements": [
        _stmt("ANALYSIS", "Player X's absence caused the decline.", ["S1", "S2"]),
    ]}
    llm = _ComposerFixture(composition, assessment=None)
    # An empty assessment cannot authorize any free analysis, with or without cue words.
    answer = ResponseComposer(llm).compose(goal, SemanticInterpretation(raw_query="why?"), projection)
    assert "absence caused" not in answer.lower()
    assert "missed 12 games" in answer and "fell 65 points" in answer


# =========================================================================== dimensions


def test_evaluation_dimensions_zero_forbidden_and_utility_retained():
    """Aggregate: unsupported synthesis/causality/laundering are zero; useful analysis survives."""
    forbidden = 0
    utility_retained = 0
    cases = [
        # (facts, qualified, statements, verdicts, forbidden_markers, expected_utility)
        (["A missed 12 games.", "OPS fell 65 points."], [],
         [_stmt("FACTUAL", "A's absence caused the OPS drop.", ["S1", "S2"])],
         [_verdict(0, epistemic="ANALYSIS", material_conclusion=True, framed_as_interpretation=False)],
         ["[S1]", "[S2]"], None),
        (["AVG .312.", "OBP .390."], [],
         [_stmt("ANALYSIS", "He had a strong season.", ["S1", "S2"])],
         [_verdict(0, material_conclusion=False)], [], "strong season"),
        (["His performance declined."],
         [_qual("A reporter says he may be hurt.", "not fully verified", kind="ASSERTION")],
         [_stmt("QUALIFIED", "He is hurt.", ["Q1"])],
         [_verdict(0, epistemic="QUALIFIED", limitation_retained=False)], [], "not fully verified"),
    ]
    for facts, qualified, statements, verdicts, markers, expected in cases:
        package = _pkg(facts=facts, qualified=qualified)
        result = _conform(statements, verdicts, package)
        for marker in markers:
            # The forbidden marker must not sit next to the unsupported conclusion.
            for line in result.text.splitlines():
                if any(cue in line.lower() for cue in ("caused", "led to", "the reason")):
                    if marker in line:
                        forbidden += 1
        if expected and expected in result.text:
            utility_retained += 1
    assert forbidden == 0
    assert utility_retained >= 2


def test_conformance_assessment_request_is_bounded_and_fails_closed():
    package = _pkg(facts=["A fact."])
    result = parse_composer_draft({"statements": [_stmt("FACTUAL", "A fact. [S1]", ["S1"])]})

    class _Broken:
        configured = True

        def complete_json(self, **kwargs):
            raise RuntimeError("provider unavailable")

    assert request_conformance_assessment(_Broken(), package, result) is None
    assert request_conformance_assessment(None, package, result) is None


def _unresolved(statement: str):
    from app.response.answer_contract import RequirementAnswer
    return RequirementAnswer(requirement_id="r_gap", statement=statement, kind="ANALYSIS",
                             required=True, coverage="UNSUPPORTED")
