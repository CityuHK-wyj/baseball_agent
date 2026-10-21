"""Second-pass adversarial review of origin governance and WSA-06 preservation."""
import pytest

from app.contracts.evidence_origin import CorroborationState as CS, DependencyState as DS
from app.contracts.models import GoalStatus, VerificationStatus
from app.response.answer_contract import AnswerPackage, AnswerFact
from app.response.composition_contract import parse_composer_draft, parse_conformance_assessment
from app.verification.composition_conformance import conform_composition
from app.verification.evidence_origin import resolve_origins, origin_presentation
from app.verification.lineage import proposition_signature
from app.verification.judge import Judge
from tests.test_evidence_origin_wsa05 import prop, evidence, resolve, OriginLLM, proposal


def test_same_span_lineage_does_not_leak_to_other_proposition():
    p1, p2 = prop(), prop("p2", "Is this contract good value?")
    fs, docs, spans = evidence(p1, ["Release records transaction.", "Article summarizes release and analyzes value."],
        lineage={0: {"basis_source_ids": ["club:release"]}, 1: {"derived_from_basis_ids": ["club:release"]}})
    assert len(resolve_origins(p1, fs, docs, spans).groups) == 1
    for f in fs:
        f.proposition_id, f.proposition_signature = p2.id, proposition_signature(p2)
    # Even reusing the same contextual span does not reuse proposition-bound lineage.
    result = resolve_origins(p2, fs, docs, spans)
    assert result.corroboration_state == CS.DEPENDENCY_UNKNOWN


def test_no_proposition_signature_in_lineage_is_unknown():
    p = prop()
    fs, docs, spans = evidence(p, ["original", "edited"], lineage={
        0: {"basis_source_ids": ["wire:story"]}, 1: {"derived_from_basis_ids": ["wire:story"]}})
    for s in spans.values():
        s.origin_lineage.proposition_signature = ""
    assert resolve_origins(p, fs, docs, spans).corroboration_state == CS.DEPENDENCY_UNKNOWN


@pytest.mark.parametrize("gate", ["target", "subject", "hard", "hash"])
def test_irrelevant_or_invalid_evidence_not_sent_to_origin_assessor(gate):
    p = prop()
    fs, docs, spans = evidence(p, ["first", "second"])
    if gate == "target":
        fs[1].checks["target_ok"] = False
    elif gate == "subject":
        fs[1].checks["subject"] = False
    elif gate == "hash":
        fs[1].span_hash = "wrong"
    else:
        fs[1].hard_conflict = True
    llm = OriginLLM()
    assert resolve_origins(p, fs, docs, spans, llm=llm) is None
    assert not llm.calls


@pytest.mark.parametrize("text,violation", [
    ("These reports share a source and he hit 87 home runs.", "INVENTED_NUMERIC"),
    ("The shared source caused the team's decline.", "UNSUPPORTED_CAUSAL"),
])
def test_even_dishonestly_cleared_origin_statement_keeps_wsa06_hard_guards(text, violation):
    package = AnswerPackage(goal_id="g", goal_status="COMPLETE", language="en",
        facts=[AnswerFact(marker="S1", claim_id="c", text="His performance declined.")],
        evidence_origins=[resolve(["same report"] * 2)])
    composition = parse_composer_draft({"statements": [{"kind": "FACTUAL", "text": text}]})
    from tests.conformance_fixtures import bound_review
    verdict = bound_review({"statements": [{"index": 0, "epistemic": "FACTUAL",
        "origin_claim": True, "origin_backed": True, "approved_claim_backed": True}]}, package, composition)
    result = conform_composition(composition, package, verdict)
    assert text not in result.text
    assert violation in {v.kind for v in result.violations}
    assert "His performance declined." in result.text


def test_missing_conformance_cannot_self_certify_origin():
    package = AnswerPackage(goal_id="g", goal_status="LIMITED", language="en",
        evidence_origins=[resolve(["original report", "second report"])])
    result = parse_composer_draft({"statements": [{"kind": "FACTUAL",
        "text": "Independent reporting confirms the event."}]})
    answer = conform_composition(result, package).text
    assert "Independent reporting confirms" not in answer
    assert "could not establish" in answer


def test_distinct_source_analyses_classify_existing_content_not_model_inference():
    p = prop("analysis", "Is the contract good value?")
    texts = ["Economist Mara's original analysis: value is good because of the incentive structure.",
             "Scout Izan's original film analysis: value is poor because of declining bat speed."]
    result = resolve(texts, p=p, llm=OriginLLM([proposal(0, 1, "SEPARATE_EVIDENTIARY_BASIS", texts,
                                                      "DISTINCT_ATTRIBUTED_ANALYSES")]), relation={1: "CONTRADICTS"})
    assert len(result.groups) == 2
    assert result.corroboration_state == CS.CONFLICTING_BASES
    assert result.relations[0].dependency_state == DS.SEPARATE_EVIDENTIARY_BASIS


def _judged(texts, classes, relation):
    from tests.test_source_authority_wsa03 import _prop, _goal_for, _role_llm
    from tests.test_web_evidence_semantics_wsa01_02 import _web_artifact
    from app.verification.evidence import verify_span
    p = _prop("roster status", info="INJURY")
    goal, _ = _goal_for(p)
    fs, docs, spans = evidence(p, texts)
    artifacts = []
    for i, source_class in enumerate(classes):
        docs[f"d{i}"].publication_date = "2026-10-01"
        assessor = _role_llm(source_class=source_class,
            content_role="PRIMARY_RECORD" if source_class == "STRUCTURED" else "REPORTING",
            evidential_basis="PRIMARY" if source_class == "STRUCTURED" else "SECONDARY",
            relation=relation[i], status="CONTRADICTED" if relation[i] == "CONTRADICTS" else "SUPPORTED")
        f = verify_span(p, spans[f"s{i}"], docs[f"d{i}"], llm=assessor)
        artifacts.append(_web_artifact(goal, p, docs[f"d{i}"], spans[f"s{i}"], f))
    return Judge(max_inspections=0).evaluate(goal, artifacts, []), artifacts


def test_many_community_reports_cannot_outvote_official_record():
    texts = [f"Shohei Ohtani roster status: community member {i} says he was optioned." for i in range(5)]
    texts.append("Shohei Ohtani roster status: the official record says he remains active, not optioned.")
    projection, artifacts = _judged(texts, ["COMMUNITY"] * 5 + ["STRUCTURED"], ["ENTAILS"] * 5 + ["CONTRADICTS"])
    assert all(a.structured_data["evidence_findings"][0]["status"] == "INSUFFICIENT" for a in artifacts[:5])
    assert projection.goal_status != GoalStatus.COMPLETE
    assert projection.evidence_origins[0].corroboration_state == CS.CONFLICTING_BASES
    assert not projection.claims
    assert len(projection.qualified_evidence) == 6


def test_actual_judge_conflict_retained_after_duplicate_support_added():
    for copies in [1, 3, 6]:
        texts = ["Shohei Ohtani roster status: official record says he is active."] * copies
        texts.append("Shohei Ohtani roster status: another official record says he is not active.")
        projection, _ = _judged(texts, ["STRUCTURED"] * len(texts), ["ENTAILS"] * copies + ["CONTRADICTS"])
        assert projection.goal_status != GoalStatus.COMPLETE
        assert projection.evidence_origins[0].corroboration_state == CS.CONFLICTING_BASES
        assert "does not decide" in origin_presentation(projection.evidence_origins)


def test_utility_one_primary_plus_derivative_context_stays_approved():
    # Origin resolution must not delete an approved derivative just to deduplicate support.
    texts = ["Shohei Ohtani roster status: official record says he is active.",
             "Shohei Ohtani roster status: official record says he is active; extra training context."]
    projection, _ = _judged(texts, ["STRUCTURED", "STRUCTURED"], ["ENTAILS", "ENTAILS"])
    assert projection.goal_status == GoalStatus.COMPLETE
    assert len(projection.claims) == 2
    assert any("extra training context" in r.span for c in projection.claims for r in c.support_refs)


def test_origin_statement_cannot_consume_the_event_premises_citation():
    package = AnswerPackage(goal_id="g", goal_status="COMPLETE", language="en",
        facts=[AnswerFact(marker="S1", text="The club announced the signing.", claim_id="c")],
        evidence_origins=[resolve(["same announcement"] * 2)])
    composition = parse_composer_draft({"statements": [{"kind": "FACTUAL",
        "text": "The club announced the signing. These reports share its announcement. [S1]"}]})
    from tests.conformance_fixtures import bound_review
    verdict = bound_review({"statements": [{"index": 0, "epistemic": "FACTUAL",
        "origin_claim": True, "origin_backed": True, "approved_claim_backed": True}]}, package, composition)
    answer = conform_composition(composition, package, verdict).text
    assert "The club announced the signing. [S1]" in answer
    assert "These reports share its announcement. [S1]" not in answer
