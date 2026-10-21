"""Real-model subject-granularity proposals; no fixtures/fallback model counted as live."""
import pytest

from app.config import get_config
from app.knowledge.store import KnowledgeStore
from app.llm.client import LLMClient
from app.semantic.goals import RequirementBuilder
from app.semantic.interpreter import SemanticInterpreter
from app.semantic.provenance import provenance_valid

pytestmark = pytest.mark.integration


@pytest.mark.parametrize('query,mode', [
    ('2023年大谷翔平和Aaron Judge常规赛打击表现如何？', 'individual'),
    ('Give individual 2023 regular-season batting lines for Aaron Judge and Shohei Ohtani.', 'individual'),
    ('大谷翔平 / Aaron Judge，2023 regular season，請各列一份打擊成績。', 'individual'),
    ('Show the 2023 regular season battng lines of Shohei Ohtani and Aaron Judge separately.', 'individual'),
    ('2023常规赛，分别列出Aaron Judge和Shohei Ohtani的本垒打数。', 'cells'),
    ('For the 2023 regular season, what is the sum of home runs by Shohei Ohtani and Aaron Judge?', 'SUM'),
    ('What is the arithmetic mean of Shohei Ohtani and Aaron Judge home-run totals in the 2023 regular season?', 'MEAN'),
    ('先列出大谷翔平与Aaron Judge在2023常规赛各自的本垒打数，然后给出两人合计。', 'mixed'),
    ('2025年大谷翔平面对左投和右投的打击率分别是多少？', 'non_scalar'),
    ('Give Aaron Judge home runs by calendar month in the 2023 regular season.', 'non_scalar'),
])
def test_live_interpreter_conserves_individual_and_combined_output_units(query, mode):
    config = get_config()
    assert config.llm.configured, 'configured real provider required'
    semantic = SemanticInterpreter(LLMClient(config.llm), knowledge=KnowledgeStore()).interpret(query)
    assert semantic.analysis_requests, semantic.model_dump()
    requests = semantic.analysis_requests
    if mode == 'individual':
        assert all(r.relation_semantics and r.relation_semantics.output_granularity == 'PER_SUBJECT'
                   for r in requests), semantic.model_dump()
        assert not any(r.operation_contract for r in requests)
    if mode == 'non_scalar':
        assert not any(r.reporting_contract or r.operation_contract for r in requests), semantic.model_dump()
    if mode in {'cells', 'mixed'}:
        cells = [c for r in requests if r.reporting_contract for c in r.reporting_contract.cells]
        assert len(cells) >= 2
        assert all(len(c.subjects) == 1 for c in cells)
        assert len({c.subjects[0] for c in cells}) == 2
    if mode in {'SUM', 'MEAN', 'mixed'}:
        contracts = [r.operation_contract for r in requests if r.operation_contract]
        expected = 'SUM' if mode == 'mixed' else mode
        assert any(c.kind == expected and len(c.operands) == 2 and
                   all(o.scope and len(o.scope.subjects) == 1 for o in c.operands)
                   for c in contracts), semantic.model_dump()
    goal = RequirementBuilder().build(semantic)
    for req in goal.requirements:
        if req.kind.value == 'ANALYSIS':
            assert provenance_valid(req.model_dump(mode='json'), bound_message=query)
