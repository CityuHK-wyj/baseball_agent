"""Authority-write-site controls promoted after the real Wikipedia diagnostic."""
from app.verification.judge import Judge
from tests.test_knowledge_authority_wsa07 import _explanation_goal, _knowledge_artifact

OPS = {'id':'GLOSSARY:OPS','type':'DEFINITION','title':'OPS','summary':'On-base Plus Slugging.'}
EV = {'id':'GLOSSARY:EV','type':'DEFINITION','title':'Exit Velocity (EV)',
      'summary':'Speed of the ball off the bat, in mph.'}


def test_unrelated_semantic_knowledge_is_not_owned_by_partial_historical_requirement():
    goal = _explanation_goal('explain causes and effects of the 1994 MLB strike')
    projection = Judge().evaluate(goal, [_knowledge_artifact([EV])], [])
    assert not projection.claims


def test_relevant_definition_cannot_launder_an_unrelated_sibling_item():
    goal = _explanation_goal('explain OPS')
    projection = Judge().evaluate(goal, [_knowledge_artifact([OPS,EV])], [])
    assert any('On-base Plus Slugging' in c.text for c in projection.claims)
    assert not any('Exit Velocity' in c.text for c in projection.claims)
