"""GA-04: provenance of an admitted semantic candidate is not whole-span coverage."""

import pytest

from tools.requirement_ancestry_fixture import root_user_requirements
from app.capabilities.analytics.conformance import constraint_coverage
from app.semantic.analytical_candidate import assess_candidate, candidate_proves, ground_candidate
from tests.test_nl_semantic_candidate_m212r4 import contract_for


def _candidate(span, *, role="BATTER", field="opposing_pitcher_hand", value="left"):
    return {"analytical_role": role,
            "restrictions": [{"dimension": field, "value": value, "source_text": span}],
            "source_text": span}


def _parents(text, year=2023):
    return root_user_requirements([dict(id="e", kind="ENTITY", source="user", value={"mention": "Ohtani"}),
            dict(id="s", kind="SEASON", source="user", value=year),
            dict(id="m", kind="MEASURE", source="user", value={"key": "home_runs", "label": "Home runs (HR)"}),
            dict(id="a", kind="ANALYSIS", source="user", value={"objective": text})], text)


@pytest.mark.parametrize("span,extra", [
    ("against lefties in two-strike counts", "two-strike"),
    ("in two-strike counts against lefties", "two-strike"),
    ("against lefties, but only with two strikes", "strikes"),
    ("against lefties under the lights", "lights"),
    ("against lefties with runners in scoring position", "scoring position"),
])
def test_admit_hand_but_conserve_extra_meaning(span, extra):
    text = f"Ohtani home runs {span} in 2023"
    grounded, issues = ground_candidate(_candidate(span), source_text=text)
    assert grounded and not issues
    assessment = assess_candidate(text, grounded, _parents(text))
    assert assessment.admitted
    assert assessment.coverage == "PARTIAL"
    assert span not in assessment.accounted_spans
    assert candidate_proves(text, grounded, _parents(text))
    contract = contract_for(text, _candidate(span), year=2023)
    assert any(p['field'] == 'p_throws' and p['value'] == 'L' for p in contract.filters)
    source = next(c for c in contract.clause_coverage if c['requirement_id'] == 'source_review')
    if extra == 'strikes' and any(p['field'] == 'strikes' and p['value'] == 2
                                  for p in contract.filters):
        # Independently parsed meaning is carried by its own governed filter.
        assert source['obligations']['filters']
    else:
        assert contract.issues and source['state'] != 'COMPLETE'
        assert extra in str(source['unresolved'])


@pytest.mark.parametrize('span,full', [('against lefties', True), ('facing portsiders', False),
                                       ('a pitcher throwing from the left side', False)])
def test_legacy_candidate_admission_is_separate_from_finite_coverage(span, full):
    text = f"Ohtani home runs {span} in 2023"
    grounded, _ = ground_candidate(_candidate(span), source_text=text)
    result = assess_candidate(text, grounded, _parents(text))
    assert result.admitted and (result.coverage == 'FULL') == full
    assert not result.accounted_spans  # provenance is never an accounting term
    contract = contract_for(text, _candidate(span), year=2023)
    assert bool(contract.issues) != full, contract.issues
    assert contract.analytical_role == 'BATTER'
    assert any(p['field'] == 'p_throws' and p['value'] == 'L' for p in contract.filters)


def test_novel_meaning_with_independently_parsed_count_is_admitted_without_erasing_count():
    span = 'facing portsiders with two strikes'
    text = f'Ohtani home runs {span} in 2023'
    grounded, _ = ground_candidate(_candidate(span), source_text=text)
    result = assess_candidate(text, grounded, _parents(text))
    assert result.admitted and result.coverage == 'PARTIAL'
    contract = contract_for(text, _candidate(span), year=2023)
    assert contract.issues  # un-attributed novel wording remains unconsumed
    assert {p['field'] for p in contract.filters} == {'p_throws', 'strikes'}


def test_novel_phrase_does_not_erase_an_unparsed_governed_count_dimension():
    span = 'facing portsiders in two-strike counts'
    text = f'Ohtani home runs {span} in 2023'
    grounded, _ = ground_candidate(_candidate(span), source_text=text)
    result = assess_candidate(text, grounded, _parents(text))
    assert result.admitted and result.coverage == 'PARTIAL'
    assert span not in result.accounted_spans
    contract = contract_for(text, _candidate(span), year=2023)
    assert any(p['field'] == 'p_throws' for p in contract.filters)
    assert contract.issues and any('two-strike' in str(c['unresolved'])
                                   for c in contract.clause_coverage)


def test_known_unsupported_modifier_does_not_prevent_independent_candidate_admission():
    span = 'facing portsiders under a closed roof'
    text = f'Ohtani home runs {span} in 2023'
    grounded, _ = ground_candidate(_candidate(span), source_text=text)
    result = assess_candidate(text, grounded, _parents(text))
    assert result.admitted and result.coverage == 'PARTIAL'
    contract = contract_for(text, _candidate(span), year=2023)
    assert any(p['field'] == 'p_throws' for p in contract.filters)
    assert contract.issues and any(c['unsupported'] for c in contract.clause_coverage)


def test_broad_span_with_harmless_task_wording_remains_useful():
    span = 'show me how Ohtani did against lefties'
    text = span + ' in 2023'
    grounded, _ = ground_candidate(_candidate(span), source_text=text)
    result = assess_candidate(text, grounded, _parents(text))
    assert result.admitted and result.coverage == 'FULL'
    contract = contract_for(text, _candidate(span), year=2023)
    assert not contract.issues, contract.issues


def test_candidate_with_contradictory_finite_meaning_is_rejected():
    span = 'against right-handed pitchers'
    text = f'Ohtani home runs {span} in 2023'
    grounded, _ = ground_candidate(_candidate(span), source_text=text)
    assert not assess_candidate(text, grounded, _parents(text)).admitted
    contract = contract_for(text, _candidate(span), year=2023)
    assert not any(p['field'] == 'p_throws' and p['value'] == 'L' for p in contract.filters)


@pytest.mark.parametrize('span', ['facing portsiders difference',
                                  'meeting the left-armed opposition change since last year'])
def test_novel_candidate_cannot_erase_existing_relational_semantics(span):
    text = f'Ohtani home runs {span} in 2023'
    grounded, _ = ground_candidate(_candidate(span), source_text=text)
    assessment = assess_candidate(text, grounded, _parents(text))
    assert assessment.admitted and assessment.coverage == 'PARTIAL'
    contract = contract_for(text, _candidate(span), year=2023)
    assert contract.issues


def test_candidate_does_not_create_comparison_bindings():
    span = 'against lefties in two-strike counts'
    text = f'Ohtani home runs {span} compared with 2024'
    grounded, _ = ground_candidate(_candidate(span), source_text=text)
    assert assess_candidate(text, grounded, _parents(text)).coverage == 'PARTIAL'
    assert not any(key in grounded for key in ('comparison', 'comparison_relation'))
    contract = contract_for(text, _candidate(span), year=2023)
    assert contract.issues


def test_overlap_cannot_duplicate_a_filter_or_erase_adjacent_content():
    span = 'against lefties with two strikes'
    text = f'Ohtani home runs {span} in 2023'
    grounded, _ = ground_candidate(_candidate(span), source_text=text)
    assert assess_candidate(text, grounded, _parents(text)).admitted
    contract = contract_for(text, _candidate(span), year=2023)
    assert not contract.issues, contract.issues
    assert [p['field'] for p in contract.filters].count('p_throws') == 1
    assert [p['field'] for p in contract.filters].count('strikes') == 1


def test_cjk_broad_span_does_not_erase_second_restriction():
    span = '面对左投和两好球'
    text = f'Ohtani本垒打{span}在2023年'
    assert constraint_coverage(span)['obligations']['filters']
    grounded, _ = ground_candidate(_candidate(span), source_text=text)
    result = assess_candidate(text, grounded, _parents(text))
    assert result.admitted
    assert result.coverage == 'PARTIAL'
    contract = contract_for(text, _candidate(span), year=2023)
    assert contract.issues or {p['field'] for p in contract.filters} >= {'p_throws', 'strikes'}
