"""WSA-08 dynamic_fact safety-contract consistency (closed-world structured first-party).

This remains a deterministic adapter: no LLM, no open-prose verification. The tests cover the
small cleanup (shared safe-URL primitive, response bound, JSON sanity, identity re-check,
lineage) and the Judge subject re-check that a fact for player A cannot satisfy player B.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from types import SimpleNamespace

import pytest
import httpx
import socket

from app.capabilities.dynamic_fact import DynamicFactTool, STATSAPI
from tools.requirement_ancestry_fixture import Goal
from app.contracts.models import (
    Artifact,
    EntityReference, IdentityLevel, RelationSemantics, RelationKind,
    PlannerNeed,
    RequirementKind,
    Scope,
    ToolRequest,
    UserRequirement,
)
from app.verification.judge import Judge


TODAY = datetime.now(timezone.utc).date().isoformat()


def _config(max_bytes: int = 1000) -> SimpleNamespace:
    return SimpleNamespace(web=SimpleNamespace(fetch_timeout=5.0, user_agent="test", max_bytes=max_bytes))


class _FakeStream(httpx.SyncByteStream):
    def __init__(self, payload: bytes, *, content_type="application/json", error: bool = False, chunks=None):
        self._payload = payload
        self.headers = {"content-type": content_type}
        self._error = error
        self._chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if self._error:
            raise RuntimeError("http error")

    def __iter__(self):
        if self._chunks is not None:
            yield from self._chunks
        else:
            yield self._payload


def _install_stream(monkeypatch, stream):
    real_client = httpx.Client
    def handler(req):
        return httpx.Response(500 if stream._error else 200, headers=stream.headers, stream=stream)
    def factory(*a,**kwargs):
        kwargs['transport'] = httpx.MockTransport(handler)
        return real_client(*a,**kwargs)
    monkeypatch.setattr(httpx,'Client',factory)
    monkeypatch.setattr(socket,'getaddrinfo',lambda host,port,*a,**k:[
        (socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',port))])


def _person_payload(player_id: int = 123, *, team="New York Yankees", name="Aaron Judge") -> bytes:
    return json.dumps({"people": [{"id": player_id, "fullName": name, "currentTeam": {"id": 147, "name": team}}]}).encode()


def test_dynamic_fact_uses_shared_safe_url_and_stream(monkeypatch):
    calls = []
    monkeypatch.setattr("app.capabilities.web_search.assert_safe_url", lambda url: calls.append(url) or url)
    import httpx
    _install_stream(monkeypatch, _FakeStream(_person_payload()))
    monkeypatch.setattr('app.capabilities.web_search._pinned_target',lambda url:
        (calls.append(url) or url,'statsapi.mlb.com',{'Host':'statsapi.mlb.com'},{}))
    fact = DynamicFactTool(_config())._current_team(123, TODAY)
    assert calls == [f"{STATSAPI}/people/123?hydrate=currentTeam"]
    assert fact["value"] == "New York Yankees"
    assert fact["response_hash"] and fact["retrieved_at"]


def test_dynamic_fact_bounds_response_size(monkeypatch):
    import httpx
    _install_stream(monkeypatch, _FakeStream(b'',chunks=[b'x'*800,b'y'*800]))
    assert DynamicFactTool(_config(max_bytes=1000))._current_team(123, TODAY) is None


def test_dynamic_fact_rejects_non_json_content_type(monkeypatch):
    import httpx
    _install_stream(monkeypatch, _FakeStream(_person_payload(),content_type='text/html'))
    assert DynamicFactTool(_config())._current_team(123, TODAY) is None


def test_dynamic_fact_rejects_mismatched_player_identity(monkeypatch):
    import httpx
    _install_stream(monkeypatch, _FakeStream(_person_payload(player_id=999)))
    assert DynamicFactTool(_config())._current_team(123, TODAY) is None


def test_dynamic_fact_transport_failure_is_not_a_fabricated_fact(monkeypatch):
    import httpx
    _install_stream(monkeypatch, _FakeStream(b'',error=True))
    assert DynamicFactTool(_config())._current_team(123, TODAY) is None


def test_dynamic_fact_artifact_records_deterministic_lineage(monkeypatch):
    import httpx
    _install_stream(monkeypatch, _FakeStream(_person_payload()))
    tool = DynamicFactTool(_config())
    result = tool.execute(ToolRequest(need_id="n", capability="dynamic_fact", objective="team",
                                      inputs={"player_ids": ["123"]}))
    artifact = result.artifact
    assert artifact.status == "ACCEPTED"
    assert artifact.provenance["endpoint"] == STATSAPI
    assert artifact.provenance["response_hashes"]["123"]
    assert artifact.provenance["retrieved_at"]
    assert artifact.actual_scope.entity_names == ["Aaron Judge"]


def _goal_for_subject(name: str) -> Goal:
    return Goal(
        statement=f"Is {name} on the team?", raw_query=f"Is {name} on the team?",
        requirements=[
            UserRequirement(id="r_ent", kind=RequirementKind.ENTITY, statement=name, value={"mention": name},
                entity_ref=EntityReference(surface=name, canonical_name=name, namespace='MLBAM',
                    canonical_id={'Aaron Judge': '592450', 'Shohei Ohtani': '660271'}[name],
                    status=IdentityLevel.CANONICAL, identity_verified=True)),
            UserRequirement(id="r_ev", kind=RequirementKind.EVIDENCE, statement=f"{name} current team",
                            value={"objective": f"{name} current team"},
                            relation_semantics=RelationSemantics(relation_kind=RelationKind.FACT,
                                relation_kinds=[RelationKind.FACT], predicate='TEAM_MEMBERSHIP')),
        ],
    )


def _dynamic_artifact(*, player_name: str, entity_id: str) -> Artifact:
    raw = _person_payload(int(entity_id), name=player_name)
    digest = sha256(raw).hexdigest()
    url = f'{STATSAPI}/people/{entity_id}?hydrate=currentTeam'
    observed = datetime.now(timezone.utc).isoformat()
    return Artifact(
        id="art_dyn", kind="DYNAMIC_FACT", need_id="n_fact", status="ACCEPTED",
        structured_data={"relation": "TEAM_MEMBERSHIP", "as_of": TODAY, "facts": [
            {"entity_id": entity_id, "player_name": player_name, "relation": "TEAM_MEMBERSHIP",
             "value": "New York Yankees", 'team_id': 147, 'as_of': TODAY,
             'response_hash': digest, 'source_url': url, "epistemic_status": "COLLECTED"},
        ], 'provider_responses': [{'url': url, 'raw_body': raw.decode(),
                                   'content_hash': digest, 'retrieved_at': observed}]},
        text_content=f"{entity_id}: New York Yankees as of {TODAY}",
        actual_scope=Scope(entities=[entity_id], entity_names=[player_name], source="MLB StatsAPI"),
    )


def _needs() -> list[PlannerNeed]:
    return [PlannerNeed(id="n_fact", objective="fact", capability="dynamic_fact", requires=["r_ev"])]


def test_dynamic_fact_for_wrong_subject_cannot_satisfy_requirement():
    projection = Judge(max_inspections=0).evaluate(
        _goal_for_subject("Aaron Judge"),
        [_dynamic_artifact(player_name="Shohei Ohtani", entity_id="660271")],
        _needs(),
    )
    evidence = next((p for p in projection.propositions if p.requirement_id == "r_ev"), None)
    # No Claim may be minted for a different player's structured fact.
    assert not projection.claims
    assert projection.goal_status.value != "COMPLETE"


def test_dynamic_fact_for_matching_subject_can_satisfy_requirement():
    projection = Judge(max_inspections=0).evaluate(
        _goal_for_subject("Aaron Judge"),
        [_dynamic_artifact(player_name="Aaron Judge", entity_id="592450")],
        _needs(),
    )
    assert projection.claims
    assert any("Aaron Judge" in claim.text for claim in projection.claims)
