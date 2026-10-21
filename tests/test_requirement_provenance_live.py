"""Live semantic utility/adversarial checks; not the independent Stage 9 audit."""
import pytest

from app.config import get_config
from app.contracts.models import AuthorityNeed, EvidenceIntent
from app.knowledge.store import KnowledgeStore
from app.llm.client import LLMClient
from app.semantic.goals import RequirementBuilder
from app.semantic.interpreter import SemanticInterpreter
from app.semantic.provenance import provenance_valid
from app.verification.propositions import build_propositions

pytestmark = pytest.mark.integration


@pytest.mark.parametrize('query,mode,authority', [
    ('Give the confirmed injury-list state for Mookie Betts today; exclude projected return dates and reactions from supporters.',
     'CONFIRMED_STATE', AuthorityNeed.OFFICIAL_STATE),
    ('我问的是贝茨现在是否在伤病名单，不是球迷评价，也不要预测。',
     'CONFIRMED_STATE', AuthorityNeed.OFFICIAL_STATE),
    ('¿Está Betts actualmente lesionado? No quiero rumores ni reacciones de aficionados.',
     'CONFIRMED_STATE', AuthorityNeed.OFFICIAL_STATE),
    ('Summarize what supporters themselves say about Betts; I want their opinions, not established team status.',
     'FIRSTHAND_ACCOUNT', AuthorityNeed.FIRSTHAND_ACCOUNT),
    ('请只总结记者关于贝茨伤情的报道内容，不要把报道当成官方确认。',
     'REPORTED_ACCOUNT', AuthorityNeed.REPORTED_ACCOUNT),
    ('What do analysts forecast for Betts returning from injury, with uncertainty retained?',
     'PREDICTION', AuthorityNeed.OFFICIAL_STATE),
])
def test_real_interpreter_preserves_requested_evidence_role(query, mode, authority):
    config = get_config()
    assert config.llm.configured, 'this live gate requires a configured provider'
    semantic = SemanticInterpreter(LLMClient(config.llm), knowledge=KnowledgeStore()).interpret(query)
    requests = [*semantic.analysis_requests, *semantic.evidence_requests]
    assert requests, semantic.model_dump()
    assert all((r.evidence_intent or EvidenceIntent()).mode == mode for r in requests)
    goal = RequirementBuilder().build(semantic)
    communicable = [p for p in build_propositions(goal) if p.kind.value in {'FACT', 'EVENT', 'GROUNDED'}
                    and p.requirement_id in {r.id for r in goal.requirements
                                              if r.kind.value in {'ANALYSIS', 'EVIDENCE'}}]
    assert communicable
    assert all(p.authority_expectation.need == authority for p in communicable)
    for req in goal.requirements:
        if req.kind.value in {'ANALYSIS', 'EVIDENCE'}:
            assert provenance_valid(req.model_dump(mode='json'), bound_message=query)
