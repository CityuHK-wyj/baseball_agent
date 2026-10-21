"""Attribution is untrusted metadata; only a bounded literal region may be accounted."""
import pytest

from app.semantic.analytical_candidate import assess_candidate, ground_candidate
from tests.test_ga04_source_provenance import _parents
from tests.test_nl_semantic_candidate_m212r4 import contract_for


def candidate(source, attributed):
    return {"analytical_role": "BATTER", "restrictions": [
        {"dimension": "opposing_pitcher_hand", "value": "left",
         "source_text": source, "attributed_span": attributed}], "source_text": source}


@pytest.mark.parametrize("source,region,residue", [
    ("facing portsiders in a windstorm", "facing portsiders", "windstorm"),
    ("in a windstorm while facing portsiders", "facing portsiders", "windstorm"),
    ("facing portsiders, but during a windstorm", "facing portsiders", "windstorm"),
    ("when the wind was unusual, facing portsiders", "facing portsiders", "unusual"),
    ("facing portsiders during a gale with the roof configuration changed", "facing portsiders", "gale"),
])
def test_partition_conserves_unattributed_text(source, region, residue):
    text = f"Ohtani home runs {source} in 2023"
    value, errors = ground_candidate(candidate(source, region), source_text=text)
    assert not errors
    assessment = assess_candidate(text, value, _parents(text))
    assert assessment.admitted and assessment.coverage == "PARTIAL"
    assert not assessment.accounted_spans  # attribution alone is never semantic proof
    assert region in str(assessment.uncovered_spans)
    assert residue in str(assessment.uncovered_spans)
    contract = contract_for(text, candidate(source, region), year=2023)
    assert any(p['field'] == 'p_throws' for p in contract.filters)
    assert contract.issues and residue in str(contract.clause_coverage)


def test_clarification_targets_residue_and_does_not_repeat_after_answer():
    from app.contracts.models import Goal, SemanticInterpretation, UserRequirement, RequirementKind
    from app.runtime.engine import Engine

    source = 'facing portsiders in a windstorm'
    text = f'Ohtani home runs {source} in 2023'
    value, _ = ground_candidate(candidate(source, 'facing portsiders'), source_text=text)
    parent = UserRequirement(kind=RequirementKind.ANALYSIS, statement=text,
                             bound_source_clause=text, value={'objective': text})
    constraint = UserRequirement(kind=RequirementKind.CONSTRAINT, statement='hand',
                                 value=value, source='inferred',
                                 derivation_kind='governed_semantic_candidate',
                                 parent_requirement_ids=[parent.id])
    goal = Goal(statement=text, raw_query=text, requirements=[parent, constraint])
    semantic = SemanticInterpretation(raw_query=text)
    clarification = Engine._clarification(None, goal, [], [], [], semantic)
    assert clarification is not None and clarification.requirement_id == parent.id
    assert 'windstorm' in clarification.question
    assert 'Ohtani' not in clarification.question
    from app.semantic.clarification import apply_semantic_delta, make_semantic
    updated = apply_semantic_delta(goal, clarification, 'PRESERVE_RESIDUE', clarification.options[1])
    assert Engine._clarification(None, updated, [], [], [], make_semantic(updated, 'answer')) is None


def test_clean_novel_attribution_can_converge():
    source = "facing portsiders"
    text = f"Ohtani home runs {source} in 2023"
    value, _ = ground_candidate(candidate(source, source), source_text=text)
    assessment = assess_candidate(text, value, _parents(text))
    assert assessment.admitted and assessment.coverage == 'PARTIAL'
    assert not assessment.accounted_spans
    assert contract_for(text, candidate(source, source), year=2023).issues


def test_broad_opaque_attribution_does_not_establish_full_coverage():
    source = 'facing portsiders in a windstorm'
    text = f'Ohtani home runs {source} in 2023'
    value, _ = ground_candidate(candidate(source, source), source_text=text)
    assessment = assess_candidate(text, value, _parents(text))
    assert assessment.admitted and assessment.coverage == 'PARTIAL'
    assert not assessment.accounted_spans
    assert contract_for(text, candidate(source, source), year=2023).issues


@pytest.mark.parametrize('region', ['portsiders elsewhere', 'portsider', 'facing portsiders in 2023'])
def test_attribution_cannot_extend_provenance_or_cut_words(region):
    source = 'facing portsiders'
    text = f'Ohtani home runs {source} in 2023'
    value, _ = ground_candidate(candidate(source, region), source_text=text)
    assert not assess_candidate(text, value, _parents(text)).admitted
