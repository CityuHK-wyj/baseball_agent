"""GA-04: provenance without attribution never contributes semantic accounting."""

import pytest

from app.capabilities.analytics.authority import resolve_authority
from app.capabilities.analytics.derivations import transformation_valid
from app.semantic.analytical_candidate import assess_candidate, ground_candidate
from tests.test_ga04_source_provenance import _parents
from tests.test_nl_semantic_candidate_m212r4 import contract_for


def _legacy(source, *, attribution=None):
    restriction = {"dimension": "opposing_pitcher_hand", "value": "left", "source_text": source}
    if attribution is not None:
        restriction["attributed_span"] = attribution
    return {"analytical_role": "BATTER", "restrictions": [restriction], "source_text": source}


@pytest.mark.parametrize("source", [
    "facing portsiders in a windstorm",
    "in a windstorm while facing portsiders",
    "facing portsiders, during a windstorm",
    "facing portsiders but during a windstorm",
    "facing portsiders during a gale with the roof configuration changed",
    "facing portsiders",
    "vs LHP",
    "aginst lefties",
    "对左投手",
    "在风暴中面对左投手",
    "facing portsiders in two-strike counts",
    "against lefties with runners in scoring position",
])
def test_legacy_provenance_is_not_accounting_or_full_without_finite_proof(source):
    text = f"Ohtani home runs {source} in 2023"
    grounded, errors = ground_candidate(_legacy(source), source_text=text)
    assert not errors
    assessment = assess_candidate(text, grounded, _parents(text))
    assert assessment.admitted  # compatibility: never reject solely for absent attribution
    assert assessment.accounted_spans == ()
    contract = contract_for(text, _legacy(source), year=2023)
    assert any(f["field"] == "p_throws" and f["value"] == "L" for f in contract.filters)
    if source in {"对左投手"}:
        # The finite adapter independently knows this binding; no candidate term is subtracted.
        assert assessment.coverage == "FULL" and not contract.issues
    else:
        assert assessment.coverage == "PARTIAL"
        if "portsiders" in source or source in {"vs LHP", "aginst lefties"}:
            assert assessment.uncovered_spans
        assert contract.issues
        source_review = next(c for c in contract.clause_coverage if c["requirement_id"] == "source_review")
        assert source_review["state"] != "COMPLETE"


def test_catalog_form_producer_without_attribution_uses_same_coverage_contract():
    source = "meeting moon-siders under a shimmering squall"
    text = f"Ohtani home runs {source} in 2023"
    raw = {"analytical_role": "BATTER", "filters": [
        {"op": "COMPARE", "field": "p_throws", "comparator": "=", "value": "L",
         "source_text": source}], "source_text": source}
    grounded, errors = ground_candidate(raw, source_text=text)
    assert not errors
    assessment = assess_candidate(text, grounded, _parents(text))
    assert assessment.admitted and assessment.coverage == "PARTIAL"
    assert not assessment.accounted_spans
    assert contract_for(text, raw, year=2023).issues


def test_legacy_finite_proof_can_complete_without_using_provenance():
    source = "against lefties"
    text = f"Ohtani home runs {source} in 2023"
    grounded, _ = ground_candidate(_legacy(source), source_text=text)
    assessment = assess_candidate(text, grounded, _parents(text))
    assert assessment.admitted and assessment.coverage == "FULL"
    assert not assessment.accounted_spans
    assert not contract_for(text, _legacy(source), year=2023).issues


def test_admission_is_not_coverage_authority():
    source = "facing portsiders in a windstorm"
    text = f"Ohtani home runs {source} in 2023"
    grounded, _ = ground_candidate(_legacy(source), source_text=text)
    parents = _parents(text)
    child = dict(id="child", kind="CONSTRAINT", source="inferred", value=grounded,
                 parent_requirement_ids=[p["id"] for p in parents],
                 derivation_kind="governed_semantic_candidate", derivation_proof="model says complete")
    assert transformation_valid(child, parents)
    admitted, _, issues = resolve_authority([*parents, child])
    assert not issues and sum(r["id"] == "child" for r in admitted) == 1
    assessment = assess_candidate(text, grounded, parents)
    assert assessment.coverage == "PARTIAL" and assessment.accounted_spans == ()
    contract = contract_for(text, _legacy(source), year=2023)
    assert contract.issues and any(c["state"] != "COMPLETE" for c in contract.clause_coverage)
    assert {p["field"] for p in contract.filters} == {"p_throws"}


def test_finite_source_cannot_admit_an_extra_candidate_filter_from_admission_alone():
    source = "against lefties"
    text = f"Ohtani home runs {source} in 2023"
    candidate = _legacy(source)
    candidate["restrictions"].append({"dimension": "strikes", "value": 2, "source_text": source})
    grounded, errors = ground_candidate(candidate, source_text=text)
    assert not errors
    assert not assess_candidate(text, grounded, _parents(text)).admitted
    contract = contract_for(text, candidate, year=2023)
    assert contract.issues
    assert {f["field"] for f in contract.filters} == {"p_throws"}


def test_explicit_attribution_still_partitions_novel_region():
    source = "in a windstorm while facing portsiders"
    text = f"Ohtani home runs {source} in 2023"
    grounded, _ = ground_candidate(_legacy(source, attribution="facing portsiders"), source_text=text)
    assessment = assess_candidate(text, grounded, _parents(text))
    assert assessment.admitted and assessment.coverage == "PARTIAL"
    assert assessment.accounted_spans == ()
    assert "windstorm" in str(assessment.uncovered_spans)
    assert contract_for(text, _legacy(source, attribution="facing portsiders"), year=2023).issues


def test_explicit_broad_attribution_does_not_resurrect_legacy_coverage():
    source = "facing portsiders in a windstorm"
    text = f"Ohtani home runs {source} in 2023"
    grounded, _ = ground_candidate(_legacy(source, attribution=source), source_text=text)
    assessment = assess_candidate(text, grounded, _parents(text))
    assert assessment.admitted and assessment.coverage == "PARTIAL"
    assert not assessment.accounted_spans
    assert contract_for(text, _legacy(source, attribution=source), year=2023).issues


def test_clean_novel_explicit_attribution_requires_confirmation_but_remains_admitted():
    source = "facing portsiders"
    text = f"Ohtani home runs {source} in 2023"
    grounded, _ = ground_candidate(_legacy(source, attribution=source), source_text=text)
    assert assess_candidate(text, grounded, _parents(text)).admitted
    assert contract_for(text, _legacy(source, attribution=source), year=2023).issues
