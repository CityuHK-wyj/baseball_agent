"""dynamic_fact: provider-backed operational facts, always scoped by as-of.

(LLM prior) != (Evidence). A user assertion is UNVERIFIED until a provider confirms it.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from app.capabilities.base import CapabilityResult, CapabilitySpec, FAMILY_WEB_EXTERNAL_RESEARCH
from app.contracts.models import Artifact, Export, Reference, ReferenceType, Scope, ToolOutcome, ToolRequest

STATSAPI = "https://statsapi.mlb.com/api/v1"


class DynamicFactTool:
    def __init__(self, config, llm=None):
        self.config = config
        self.llm = llm
        self.spec = CapabilitySpec(
            name="dynamic_fact",
            description="Resolve operational facts such as current team membership.",
            accepts=["PLAYER_ID_SET", "ENTITY_MAPPING"],
            produces=["DYNAMIC_FACT", "TEAM_MEMBERSHIP_SET"],
            required_inputs=["PLAYER_ID_SET"],
            # A structured external-record adapter for the web/external family, not a peer source.
            family=FAMILY_WEB_EXTERNAL_RESEARCH,
        )

    def execute(self, request: ToolRequest, ctx=None) -> CapabilityResult:
        relation = str(request.inputs.get("relation") or "TEAM_MEMBERSHIP").upper()
        as_of = request.inputs.get("as_of") or datetime.now(timezone.utc).date().isoformat()
        if relation != 'TEAM_MEMBERSHIP':
            return CapabilityResult(None, ToolOutcome(status='BLOCKED', code='UNSUPPORTED_RELATION',
                message='this provider adapter only collects current team membership',
                need_id=request.need_id, tool_request_id=request.id, classification='UNSUPPORTED_ANALYSIS'))

        ids: list[int] = []
        for binding in request.bindings:
            if binding.export_type != "PLAYER_ID_SET":
                continue
            export = ctx.find_export(binding.export_id) if ctx else None
            if export:
                ids.extend(int(i) for i in export.payload.get("ids", []))
        ids.extend(int(i) for i in request.inputs.get("player_ids", []) if str(i).isdigit())
        ids = sorted(set(ids))
        if not ids:
            return CapabilityResult(
                None,
                ToolOutcome(status="BLOCKED", code="INPUT_UNRESOLVED", message="dynamic_fact needs a PLAYER_ID_SET", need_id=request.need_id, tool_request_id=request.id, classification="WRONG_BINDING"),
            )

        facts: list[dict[str, Any]] = []
        unavailable = False
        for player_id in ids[:5]:
            fact = self._current_team(player_id, as_of)
            if fact is None:
                unavailable = True
                continue
            facts.append(fact)

        if not facts:
            code = "SOURCE_UNAVAILABLE" if unavailable else "NOT_FOUND"
            return CapabilityResult(
                None,
                ToolOutcome(status="FAILED" if unavailable else "NO_DATA", code=code, message="no dynamic fact could be resolved", retryable=unavailable, need_id=request.need_id, tool_request_id=request.id, classification="RETRYABLE_SOURCE" if unavailable else "INSUFFICIENT_EVIDENCE"),
            )

        # Structured lineage: which endpoint/record produced each fact, when it was retrieved,
        # and the exact response hash. This is deterministic provenance, not an EvidenceSpan.
        provenance = {
            "capability": "dynamic_fact",
            "provider": "MLB StatsAPI",
            "endpoint": STATSAPI,
            "as_of": as_of,
            "requested_entity_ids": [str(i) for i in ids],
            "request_id": request.id,
            "response_hashes": {f["entity_id"]: f.get("response_hash", "") for f in facts},
            "retrieved_at": next((f.get("retrieved_at") for f in facts if f.get("retrieved_at")), ""),
        }
        artifact = Artifact(
            kind="DYNAMIC_FACT",
            structured_data={"relation": 'TEAM_MEMBERSHIP', "as_of": as_of, "facts": facts,
                             "provider_responses": [f['provider_response'] for f in facts if f.get('provider_response')]},
            text_content="; ".join(f"{f['entity_id']}: {f['value']} as of {as_of}" for f in facts),
            exports=[
                Export(
                    id=f"exp_{request.id}_fact",
                    type="DYNAMIC_FACT",
                    payload={"facts": facts, "as_of": as_of},
                    contract={"epistemic": "COLLECTED", "relation": 'TEAM_MEMBERSHIP'},
                ),
                Export(
                    id=f"exp_{request.id}_membership",
                    type="TEAM_MEMBERSHIP_SET",
                    payload={"facts": facts, "as_of": as_of},
                    contract={"relation": "TEAM_MEMBERSHIP"},
                ),
            ],
            provenance=provenance,
            references=[Reference(ref_type=ReferenceType.WEB_EVIDENCE, ref_id=f"statsapi:{f['entity_id']}", source=f.get("source_url", STATSAPI), span=f.get("value", "")) for f in facts],
            requested_scope=Scope(entities=[str(i) for i in ids]),
            actual_scope=Scope(entities=[str(f["entity_id"]) for f in facts], source="MLB StatsAPI",
                               entity_names=[str(f.get("player_name") or "") for f in facts]),
            confidence="STRONG",
            status="ACCEPTED",
            need_id=request.need_id,
        )
        return CapabilityResult(artifact, ToolOutcome(status="SUCCESS", code="OK", message=f"{len(facts)} dynamic fact(s)", need_id=request.need_id, tool_request_id=request.id))

    def _current_team(self, player_id: int, as_of: str) -> dict[str, Any] | None:
        import httpx

        # currentTeam is not a historical endpoint. The caller's requested date is
        # not a provider temporal attestation and cannot be echoed into factual scope.
        observed_date = datetime.now(timezone.utc).date().isoformat()
        if as_of != observed_date:
            return None
        from app.capabilities.web_search import assert_safe_url

        web = getattr(self.config, "web", None)
        timeout = getattr(web, "fetch_timeout", 15.0) if web is not None else 15.0
        user_agent = getattr(web, "user_agent", "BaseballAgent/0.1") if web is not None else "BaseballAgent/0.1"
        max_bytes = getattr(web, "max_bytes", 1_000_000) if web is not None else 1_000_000
        url = f"{STATSAPI}/people/{player_id}?hydrate=currentTeam"
        try:
            from app.web.transport import request
            # This fixed native provider's origin is part of the factual receipt.
            # A redirected body must not be relabeled as a StatsAPI observation.
            response = request('GET',url,timeout=timeout,max_bytes=max_bytes,follow_redirects=False,
                               headers={'User-Agent':user_agent})
            response.raise_for_status()
            if 'json' not in response.headers.get('content-type','').lower():
                return None
            raw = response.content
            body_text = raw.decode('utf-8')
            payload = json.loads(body_text)
        except Exception:  # noqa: BLE001 - a transport/parse failure is never a fabricated fact
            return None
        if not isinstance(payload, dict):
            return None
        people = payload.get("people") or []
        if not isinstance(people, list) or len(people) != 1 or not isinstance(people[0], dict):
            return None
        person = people[0]
        # Closed-world identity re-check: the provider record must be the requested player.
        try:
            if type(person.get('id')) is not int or person['id'] != int(player_id):
                return None
        except (TypeError, ValueError):
            return None
        team = person.get("currentTeam") or {}
        if not isinstance(team, dict):
            return None
        team_name = team.get("name")
        if team_name is not None and (not isinstance(team_name, str) or not team_name.strip()):
            return None
        if team_name and (type(team.get('id')) is not int or team['id'] <= 0
                          or not isinstance(person.get('fullName'), str) or not person['fullName'].strip()):
            return None
        retrieved_at = datetime.now(timezone.utc).isoformat()
        response_hash = hashlib.sha256(raw).hexdigest()
        if not team_name:
            return {
                "entity_id": str(player_id),
                "relation": "TEAM_MEMBERSHIP",
                "value": "no current team recorded",
                "epistemic_status": "NOT_FOUND",
                "source_url": url,
                "response_hash": response_hash,
                "retrieved_at": retrieved_at,
            }
        return {
            "entity_id": str(player_id),
            "relation": "TEAM_MEMBERSHIP",
            "value": team_name,
            "team_id": team.get("id"),
            "player_name": person.get("fullName", ""),
            "epistemic_status": "COLLECTED",
            "as_of": retrieved_at[:10],
            "provider_response": {'url': url, 'raw_body': body_text,
                                  'content_hash': response_hash, 'retrieved_at': retrieved_at},
            "source_url": url,
            "response_hash": response_hash,
            "retrieved_at": retrieved_at,
        }
