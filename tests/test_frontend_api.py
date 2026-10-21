"""Deterministic HTTP/SSE contract tests; actual Engine flow is tested separately."""
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.presentation import present
from app.contracts.models import (AnswerProjection, AnswerProposition, Artifact, Claim, Clarification,
                                  CoverageState, Goal, GoalStatus, PropositionKind, Reference,
                                  ReferenceType, RequirementKind, Scope, UserRequirement)
from app.verification.propositions import build_propositions
from app.persistence.store import PersistenceStore
from app.runtime.conversation import ConversationManager


class EngineFixture:
    def __init__(self, path):
        self.conversation = ConversationManager(PersistenceStore(path / "conversations.db"))
        self.inputs = []

    def ask(self, cid, text):
        self.inputs.append(text)
        waiting = text == "ambiguous"
        partial = text == "partial"
        req = UserRequirement(id="req-a", kind=RequirementKind.ANALYSIS, statement="Approved result")
        other = UserRequirement(id="req-b", kind=RequirementKind.ANALYSIS, statement="Unresolved result")
        goal = Goal(statement=text, raw_query=text, requirements=[req, other] if partial else [req])
        goal.propositions = [AnswerProposition(goal_id=goal.id, requirement_id=req.id, statement=req.statement,
                             kind=PropositionKind.VALUE, coverage=CoverageState.SATISFIED)]
        if partial:
            goal.propositions.append(AnswerProposition(goal_id=goal.id, requirement_id=other.id,
                                                        statement=other.statement, coverage=CoverageState.BLOCKED))
        artifact = Artifact(id="art-approved", kind="DATABASE_RESULT", structured_data={
            "rows": [{"name": "A", "score": 2}, {"name": "B", "score": 1}],
            "schema": [{"name": "name", "type": "VARCHAR"}, {"name": "score", "type": "DOUBLE"}],
            "output_columns": ["name", "score"], "source": "PARQUET", "sql": "SECRET",
            "execution_receipt": {"validated_ir": "SECRET"},
        })
        rejected = Artifact(id="art-rejected", kind="DATABASE_RESULT", structured_data={"rows": [{"secret": 99}]})
        claim = Claim(text="A = 2", support_refs=[Reference(ref_type=ReferenceType.ARTIFACT, ref_id=artifact.id, source="artifact")])
        clarification = Clarification(question="Which one?", options=["A", "B"], goal_id=goal.id, requirement_id=req.id) if waiting else None
        projection = AnswerProjection(goal_id=goal.id,
                                      goal_status=GoalStatus.WAITING_FOR_USER if waiting else GoalStatus.LIMITED if partial else GoalStatus.COMPLETE,
                                      claims=[] if waiting else [claim], clarification=clarification,
                                      supporting_artifact_ids=[artifact.id, rejected.id])
        result = SimpleNamespace(goal=goal, projection=projection, semantic=SimpleNamespace(language="en"),
                                 answer="Which one?" if waiting else "Approved result", artifacts=[artifact, rejected])
        self.conversation.commit(cid, user_text=text, goal=goal, answer=result.answer, projection=projection)
        return result


def consume(client, rid, cursor=None):
    response = client.get(f"/api/runs/{rid}/events", headers={"Last-Event-ID": cursor} if cursor else {})
    assert response.status_code == 200
    import json
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def test_send_stream_clarification_replay_and_resolution(tmp_path):
    fake = EngineFixture(tmp_path)
    with TestClient(create_app(fake)) as client:
        cid = client.post("/api/conversations").json()["conversation_id"]
        response = client.post(f"/api/conversations/{cid}/messages", json={"text": "ambiguous"})
        assert response.status_code == 202
        events = consume(client, response.json()["run_id"])
        assert [event["type"] for event in events][-2:] == ["answer.partial", "clarification.required"]
        question = events[-1]["payload"]["clarification"]
        assert question["options"][0]["option_id"] == "choice:0"
        assert consume(client, response.json()["run_id"], events[-2]["id"]) == events[-1:]
        assert client.post(f"/api/conversations/{cid}/messages", json={"text": "oops"}).status_code == 409
        assert client.post(f"/api/conversations/{cid}/clarifications/bad/responses", json={"selected_option_ids": ["choice:0"]}).status_code == 409
        assert client.post(f"/api/conversations/{cid}/clarifications/{question['id']}/responses", json={"selected_option_ids": ["choice:99"]}).status_code == 422
        resumed = client.post(f"/api/conversations/{cid}/clarifications/{question['id']}/responses", json={"selected_option_ids": ["choice:1"]})
        assert resumed.status_code == 202
        events = consume(client, resumed.json()["run_id"])
        assert "clarification.resolved" in [event["type"] for event in events]
        assert events[-1]["type"] == "answer.completed"
        assert fake.inputs == ["ambiguous", "B"]  # server-owned label, never a semantic browser ID
        assert client.post(f"/api/conversations/{cid}/clarifications/{question['id']}/responses", json={"free_text": "A"}).status_code == 409
        assert client.get(f"/api/conversations/{cid}").json()["pending_clarification"] is None


def test_free_text_clarification_and_invalid_response(tmp_path):
    fake = EngineFixture(tmp_path)
    with TestClient(create_app(fake)) as client:
        cid = client.post('/api/conversations').json()['conversation_id']
        run = client.post(f'/api/conversations/{cid}/messages', json={'text': 'ambiguous'}).json()['run_id']
        request = consume(client, run)[-1]['payload']['clarification']
        assert client.post(f"/api/conversations/{cid}/clarifications/{request['id']}/responses",
                           json={'selected_option_ids': ['choice:0'], 'free_text': 'B'}).status_code == 422
        reply = client.post(f"/api/conversations/{cid}/clarifications/{request['id']}/responses",
                            json={'free_text': 'My team is Seattle'})
        assert reply.status_code == 202
        assert consume(client, reply.json()['run_id'])[-1]['type'] == 'answer.completed'
        assert fake.inputs[-1] == 'My team is Seattle'


def test_table_partial_and_private_data(tmp_path):
    with TestClient(create_app(EngineFixture(tmp_path))) as client:
        cid = client.post("/api/conversations").json()["conversation_id"]
        ack = client.post(f"/api/conversations/{cid}/messages", json={"text": "partial"}).json()
        events = consume(client, ack["run_id"])
        tables = [e["payload"]["artifact"] for e in events if e["type"] == "artifact.created"]
        assert len(tables) == 1 and tables[0]["payload"]["rows"][0]["score"] == 2
        assert "SECRET" not in str(events) and "art-rejected" not in str(events)
        answer = events[-1]["payload"]["answer"]
        assert answer["status"] == "LIMITED"
        assert len(answer["requirements"]) == 2
        assert answer["artifacts"][0]["type"] == "table"
        assert client.get(f"/api/runs/{ack['run_id']}/events?after=other:1").status_code == 400
        assert client.post(f"/api/conversations/{cid}/messages", json={"text": "\u0000"}).status_code == 422
        invalid = client.post(f"/api/conversations/{cid}/messages", json={"wrong": "shape"})
        assert invalid.status_code == 422 and invalid.json()["error"]["category"] == "validation"


def test_present_projects_only_claimed_partial_table():
    """A real execution receipt + Judge claim projects one safe partial table.

    The unclaimed sibling artifact and any internal receipt/IR/SQL fields must never
    reach the presentation envelope, even when the overall status is LIMITED.
    """
    from tests.m212_receipt_fixture import with_receipt
    from app.api.presentation import present

    goal = Goal(
        statement='mixed', raw_query='mixed request',
        requirements=[
            UserRequirement(id='a', kind=RequirementKind.ANALYSIS, statement='Report batter walks',
                            value={'objective': 'Report batter walks'}),
            UserRequirement(id='s', kind=RequirementKind.SEASON, statement='2025', value=2025),
            UserRequirement(id='m', kind=RequirementKind.MEASURE, statement='walks', value={'key': 'walks'}),
        ],
    )
    goal.propositions = build_propositions(goal)
    artifact = with_receipt(Artifact(
        id='art-approved', kind='DATABASE_RESULT', status='ACCEPTED', need_id='na',
        structured_data={'rows': [{'walks': 19}], 'row_count': 1,
                         'schema': [{'name': 'walks', 'type': 'INT'}], 'output_columns': ['walks'],
                         'sql': 'SELECT SECRET'},
        text_content='walks\n19', actual_scope=Scope(season=2025),
    ))
    claim = Claim(text='walks = 19',
                  support_refs=[Reference(ref_type=ReferenceType.ARTIFACT, ref_id=artifact.id, source='artifact')],
                  proposition_ids=[p.id for p in goal.propositions if p.requirement_id == 'a'])
    projection = AnswerProjection(goal_id=goal.id, goal_status=GoalStatus.LIMITED, claims=[claim],
                                  supporting_artifact_ids=[artifact.id])
    result = SimpleNamespace(goal=goal, projection=projection, semantic=SimpleNamespace(language='en'),
                             answer='walks: 19', artifacts=[artifact], outcomes=[])
    view = present(result)
    assert view.status == 'LIMITED'
    assert len(view.artifacts) == 1
    assert view.artifacts[0].type == 'table'
    assert view.artifacts[0].payload.rows == [{'walks': 19}]
    assert 'SECRET' not in view.model_dump_json()
    assert 'execution_receipt' not in view.model_dump_json()
