"""entity_resolution: mentions -> iterative identity (candidate / resolved / canonical).

Resolution order: curated ACTIVE knowledge alias -> LLM name normalization + player
directory -> direct directory search. Ambiguity is reported, never silently guessed.

ADR 0015: a dictionary miss is not an understanding failure. The tool always emits an
identity artifact that carries *candidate* identities (from the model prior / user wording)
as well as any *resolved* / *canonical* identity. The ``PLAYER_ID_SET`` export stays empty
when no canonical id exists, so the Binder still blocks database execution; the
``ENTITY_MAPPING`` export lets web research and knowledge lookup continue with names.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.capabilities.base import CapabilityResult, CapabilitySpec
from app.contracts.models import (
    Artifact,
    Export,
    IdentityLevel,
    Reference,
    ReferenceType,
    Scope,
    ToolOutcome,
    ToolRequest,
)
from app.semantic.goals import NON_ENTITY_HINTS

SYSTEM_PROMPT = """You normalize baseball entity mentions (often Chinese) to canonical MLB names.
Return JSON: {"entities":[{"mention":"大谷翔平","canonical_en":"Shohei Ohtani","type":"player","role":"BATTER|PITCHER|TWO_WAY|UNKNOWN","confidence":"high|medium|low"}]}
Rules:
- canonical_en must be the player's official English name as used by MLB, e.g. "Shohei Ohtani".
- role is the most relevant role for a statistics question. Two-way players: choose based on the question,
  otherwise "TWO_WAY".
- If the mention is a team, league or concept, set type accordingly and canonical_en to the English name.
- Never invent players. If unsure, return confidence "low".
"""

PITCHER_HINTS = ("投手", "pitching", "pitcher", "pitch", "era", "防御率", "三振率", "球速", "whiff")
BATTER_HINTS = ("打者", "打击", "batting", "hitter", "batter", "打击率", "本垒打", "ops", "exit velocity", "击球")


class EntityResolutionTool:
    def __init__(self, store, directory, llm):
        self.store = store
        self.directory = directory
        self.llm = llm
        self.spec = CapabilitySpec(
            name="entity_resolution",
            description="Resolve a player/team mention to an identity level (candidate/resolved/canonical).",
            accepts=["SEARCH_QUERY"],
            produces=["ENTITY_MAPPING", "PLAYER_ID_SET"],
            required_inputs=[],
        )

    def execute(self, request: ToolRequest, ctx=None) -> CapabilityResult:
        mentions = request.inputs.get("mentions") or []
        raw_query = request.inputs.get("raw_query", "") or request.objective
        default_role = str(request.inputs.get("role") or "BATTER").upper()

        if not mentions and request.inputs.get("query"):
            mentions = [{"mention": request.inputs["query"], "type_hint": "unknown"}]
        # Statistics, dimensions and concepts are analysis inputs, not resolvable subjects.
        mentions = [m for m in mentions if str(m.get("type_hint", "unknown")).lower() not in NON_ENTITY_HINTS]
        if not mentions:
            return CapabilityResult(
                None,
                ToolOutcome(
                    status="NO_DATA",
                    code="VALID_EMPTY",
                    message="no resolvable player/team entity was requested",
                    need_id=request.need_id,
                    tool_request_id=request.id,
                    classification="VALID_EMPTY",
                ),
            )

        resolved: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []
        unresolved: list[str] = []
        ambiguous: list[dict[str, Any]] = []
        identity: dict[str, Any] = {}

        normalized = self._normalize_mentions(mentions, raw_query)

        for mention in mentions:
            text = mention.get("mention") or ""
            type_hint = mention.get("type_hint", "unknown")
            info = normalized.get(text, {})
            text_role = self._role_from_text(text, raw_query)
            info_role = str(info.get("role") or "").upper()
            if info_role not in {"BATTER", "PITCHER", "TWO_WAY"}:
                info_role = ""
            role = (text_role or info_role or default_role).upper()
            if role == "TWO_WAY":
                role = default_role if default_role in {"BATTER", "PITCHER"} else "BATTER"

            # F1 (ADR 0018): a verified contextual identity is used directly. The surface
            # pronoun is preserved as the key, so the requirement/judge still recognise it,
            # but it can never be reported as unresolved while a canonical id is known.
            contextual_id = mention.get("canonical_id")
            contextual_name = str(mention.get("canonical_name") or "").strip()
            reference_status = str(mention.get("status") or "").upper()
            namespace = str(mention.get("namespace") or "MLBAM").upper()
            if contextual_id and reference_status in {"CANONICAL", "RESOLVED"} and namespace == "MLBAM":
                try:
                    player_id = int(contextual_id)
                except (TypeError, ValueError):
                    player_id = None
                if player_id is not None:
                    name = contextual_name or text
                    resolved.append(
                        {
                            "mention": text,
                            "id": player_id,
                            "name": name,
                            "role": role,
                            "source": "context",
                            "antecedent": mention.get("antecedent"),
                        }
                    )
                    identity[text] = {
                        "level": IdentityLevel.CANONICAL.value,
                        "id": player_id,
                        "name": name,
                        "role": role,
                        "source": "context",
                    }
                    continue

            # A carried contextual name (no id yet) is a legitimate lookup key even when
            # the user's surface form is a pronoun.
            lookup_text = contextual_name or text
            item = self.store.match_alias(lookup_text) or self.store.match_alias(text)
            if item is not None and item.type == "ENTITY_ALIAS":
                player_id = item.payload.get("player_id")
                if player_id:
                    default_for_player = str(item.payload.get("default_role") or "").upper()
                    if item.payload.get("two_way"):
                        role = default_role if default_role in {"BATTER", "PITCHER"} else "BATTER"
                    elif text_role:
                        role = text_role
                    elif info_role in {"BATTER", "PITCHER"}:
                        role = info_role
                    elif default_for_player in {"BATTER", "PITCHER"}:
                        role = default_for_player
                    resolved.append({"mention": text, "id": int(player_id), "name": item.title, "role": role, "source": "knowledge"})
                    identity[text] = {"level": IdentityLevel.CANONICAL.value, "id": int(player_id), "name": item.title, "role": role}
                    continue

            canonical = info.get("canonical_en") or contextual_name or text
            confidence = str(info.get("confidence") or "low")
            if info.get("type") == "team" or type_hint == "team":
                candidates.append({"mention": text, "id": None, "name": canonical, "role": "TEAM", "type": "team", "source": "llm", "confidence": confidence})
                identity[text] = {"level": IdentityLevel.CANDIDATE.value, "id": None, "name": canonical, "type": "team"}
                continue

            directory_candidates = self._lookup_candidates(canonical) or self._lookup_candidates(lookup_text)
            if not directory_candidates:
                # No canonical id in the local dictionary. Keep the model/user name as a
                # candidate identity so web research and knowledge lookup can continue.
                candidates.append({"mention": text, "id": None, "name": canonical, "role": role, "source": "llm" if info else "user_wording", "confidence": confidence})
                identity[text] = {"level": IdentityLevel.CANDIDATE.value, "id": None, "name": canonical, "role": role}
                continue
            best = self._choose(canonical, directory_candidates)
            if best is None:
                ambiguous.append({"mention": text, "options": [f"{c['id']}:{c['name']}" for c in directory_candidates[:4]]})
                candidates.append({"mention": text, "id": None, "name": canonical, "role": role, "source": "ambiguous", "options": [f"{c['id']}:{c['name']}" for c in directory_candidates[:4]]})
                identity[text] = {"level": IdentityLevel.CANDIDATE.value, "id": None, "name": canonical, "role": role, "ambiguous": True}
                continue
            resolved.append({"mention": text, "id": best["id"], "name": best["name"], "role": role, "source": "directory"})
            identity[text] = {"level": IdentityLevel.CANONICAL.value, "id": best["id"], "name": best["name"], "role": role}
            # Runtime discovery is recorded as CandidateKnowledge; it is never ACTIVE
            # until an administrator reviews it.
            try:
                self.store.record_candidate(
                    {
                        "id": f"PLAYER:{best['id']}",
                        "type": "ENTITY_ALIAS",
                        "title": best["name"],
                        "aliases": list({text, lookup_text}),
                        "language": "zh" if any("\u4e00" <= ch <= "\u9fff" for ch in text) else "en",
                        "summary": f"Runtime-resolved alias for mention '{text}'.",
                        "payload": {"player_id": best["id"], "default_role": role},
                    },
                    origin="entity_resolution",
                    evidence=[raw_query],
                )
            except Exception:  # noqa: BLE001 - never fail resolution because of governance bookkeeping
                pass

        for mention in mentions:
            text = mention.get("mention") or ""
            if text and text not in identity:
                unresolved.append(text)

        artifact = self._artifact(request, resolved, candidates, unresolved, ambiguous, identity)

        if ambiguous:
            return CapabilityResult(
                None,
                ToolOutcome(
                    status="BLOCKED",
                    code="IDENTITY_AMBIGUOUS",
                    message="multiple players match the mention",
                    need_id=request.need_id,
                    tool_request_id=request.id,
                    classification="IDENTITY_AMBIGUITY",
                ),
                extra_artifacts=[artifact],
            )
        if resolved:
            return CapabilityResult(
                artifact,
                ToolOutcome(status="SUCCESS", code="OK", message=f"resolved {len(resolved)} canonical entity/entities", need_id=request.need_id, tool_request_id=request.id),
            )
        # Candidate-only is a valid research starting point, not a failure. The empty
        # PLAYER_ID_SET export still blocks database execution at the Binder.
        return CapabilityResult(
            artifact,
            ToolOutcome(
                status="SUCCESS",
                code="CANDIDATE_ONLY",
                message=f"{len(candidates)} candidate identity/identities without a canonical id",
                need_id=request.need_id,
                tool_request_id=request.id,
                classification="INSUFFICIENT_EVIDENCE",
            ),
        )

    # ------------------------------------------------------------------ helpers
    def _normalize_mentions(self, mentions: list[dict[str, Any]], raw_query: str) -> dict[str, dict[str, Any]]:
        if not self.llm or not self.llm.configured:
            return {}
        user = json.dumps({"mentions": mentions, "question": raw_query}, ensure_ascii=False)
        try:
            result = self.llm.complete_json(role="semantic", system=SYSTEM_PROMPT, user=user, max_tokens=500)
        except Exception:  # noqa: BLE001
            return {}
        mapping: dict[str, dict[str, Any]] = {}
        for entry in result.data.get("entities", []):
            if isinstance(entry, dict) and entry.get("mention"):
                mapping[entry["mention"]] = entry
        return mapping

    def _role_from_text(self, text: str, raw_query: str) -> str | None:
        haystack = f"{text} {raw_query}".lower()
        if any(hint in haystack for hint in PITCHER_HINTS):
            return "PITCHER"
        if any(hint in haystack for hint in BATTER_HINTS):
            return "BATTER"
        return None

    def _lookup_candidates(self, name: str) -> list[dict[str, Any]]:
        if not name:
            return []
        return self.directory.search_by_name(name, limit=6)

    def _choose(self, canonical: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
        if len(candidates) == 1:
            return candidates[0]
        target_tokens = _token_set(canonical)
        if target_tokens:
            exact = [c for c in candidates if _token_set(c["name"]) == target_tokens]
            if len(exact) == 1:
                return exact[0]
            superset = [c for c in candidates if target_tokens <= _token_set(c["name"])]
            if len(superset) == 1:
                return superset[0]
            if len(superset) > 1:
                return None  # deliberately ambiguous
        # Surname-only fallback: only accept if exactly one candidate carries it.
        surname_matches = [c for c in candidates if target_tokens and target_tokens <= _token_set(c["name"])]
        if len(surname_matches) == 1:
            return surname_matches[0]
        return None

    def _artifact(self, request: ToolRequest, resolved, candidates, unresolved, ambiguous, identity) -> Artifact:
        players = [r for r in resolved if r.get("id")]
        ids = sorted({int(r["id"]) for r in players})
        names = [r["name"] for r in players]
        candidate_names = [c["name"] for c in candidates if c.get("name")]
        roles = sorted({r["role"] for r in resolved})
        all_names = names + [n for n in candidate_names if n not in names]
        return Artifact(
            kind="ENTITY_RESOLUTION",
            structured_data={
                "resolved": resolved,
                "candidates": candidates,
                "unresolved": unresolved,
                "ambiguous": ambiguous,
                "identity": identity,
            },
            text_content="; ".join(f"{r['mention']} -> {r['name']} ({r['id']})" for r in resolved)
            or "; ".join(f"{c['mention']} ~ {c['name']} (candidate)" for c in candidates),
            exports=[
                Export(
                    id=f"exp_{request.id}_players",
                    type="PLAYER_ID_SET",
                    payload={
                        "ids": ids,
                        "names": names,
                        "role": roles[0] if len(roles) == 1 else "MIXED",
                        "resolved": resolved,
                    },
                    contract={"namespace": "MLBAM", "cardinality": "SET", "entity": "PLAYER"},
                ),
                Export(
                    id=f"exp_{request.id}_mapping",
                    type="ENTITY_MAPPING",
                    payload={"resolved": resolved, "candidates": candidates, "identity": identity},
                    contract={"namespace": "MLBAM", "cardinality": "MAP"},
                ),
            ],
            provenance={"capability": "entity_resolution", "request_id": request.id},
            references=[Reference(ref_type=ReferenceType.TOOL_REQUEST, ref_id=request.id, source="runtime")],
            requested_scope=Scope(entity_names=[m.get("mention", "") for m in request.inputs.get("mentions", [])]),
            actual_scope=Scope(entities=[str(i) for i in ids], entity_names=all_names),
            confidence="STRONG" if players else ("ACCEPTABLE" if candidates else "WEAK"),
            status="ACCEPTED" if (players or candidates) else "PARTIAL",
            need_id=request.need_id,
        )


def _normalize(text: str) -> str:
    return "".join(ch for ch in (text or "").lower() if ch.isalnum())


def _token_set(text: str) -> set[str]:
    return {t for t in re.split(r"[^0-9a-zA-Z\u4e00-\u9fff]+", (text or "").lower()) if t}
