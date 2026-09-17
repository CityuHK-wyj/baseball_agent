"""Administrator-governed promotion of candidate knowledge.

READ PATH : planner / semantic / resolver -> KnowledgeStore -> ACTIVE approved knowledge.
WRITE PATH: runtime discovery -> CandidateKnowledge -> Review Queue -> administrator
            review -> APPROVED/ACTIVE KnowledgeStore.

Runtime agents never write ACTIVE knowledge directly. A confidence threshold is not
approval. This service is the only path that creates ACTIVE knowledge from a candidate,
and it surfaces conflicts instead of overwriting silently.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.knowledge.candidates import (CandidateKnowledge, CandidateKnowledgeStore,
                                      CandidateScope)
from app.models.knowledge import AUTHORITY_RANK, KnowledgeItem

# Candidate categories -> the stable Shared Knowledge vocabulary.
_CATEGORY_TO_TYPE: dict[str, str] = {
    "CANONICAL_FACT": "TERM",
    "ENTITY_ALIAS": "ALIAS",
    "ALIAS": "ALIAS",
    "DEFINITION": "TERM",
    "TERM": "TERM",
    "HISTORICAL_EVENT": "HISTORICAL_CONTEXT",
    "RULE": "RULE",
    "COMMUNITY_REFERENCE": "COMMUNITY_CREATOR",
    "COMMUNITY_OPINION": "HISTORICAL_CONTEXT",
    "SCOUTING_NOTE": "SCOUTING_CONCEPT",
    "TACTICAL_CONCEPT": "SCOUTING_CONCEPT",
    "SOURCE_INTERPRETATION": "SOURCE",
    "CONTEXT_REFERENCE": "TERM",
}


class GovernanceError(RuntimeError):
    pass


def _norm(value: str) -> str:
    return (value or "").strip().casefold()


class KnowledgeGovernance:
    def __init__(self, candidates: CandidateKnowledgeStore, knowledge) -> None:
        self._candidates = candidates
        self._knowledge = knowledge

    # -- read path ---------------------------------------------------------
    def list(self, status: str | None = None) -> tuple[CandidateKnowledge, ...]:
        return self._candidates.list(status=status)

    def get(self, candidate_id: str) -> CandidateKnowledge | None:
        return self._candidates.get(candidate_id)

    def inspect(self, candidate_id: str) -> dict[str, Any]:
        candidate = self._candidates.get(candidate_id)
        if candidate is None:
            raise KeyError(f"Unknown candidate {candidate_id!r}")
        return {"candidate": candidate, "conflicts": self.conflicts(candidate)}

    def active_matches(self, surface: str) -> tuple[KnowledgeItem, ...]:
        """Only ACTIVE approved knowledge is retrieval-authoritative."""
        try:
            return tuple(match.item for match in self._knowledge.search(surface, max_items=8))
        except Exception:  # noqa: BLE001 - retrieval failure yields no matches
            return ()

    # -- conflicts ---------------------------------------------------------
    def conflicts(self, candidate: CandidateKnowledge) -> tuple[str, ...]:
        notes: list[str] = []
        for item in self.active_matches(candidate.surface):
            names = {_norm(name) for name in item.names()}
            if _norm(candidate.surface) not in names:
                continue
            if _norm(candidate.meaning) and _norm(candidate.meaning) != _norm(item.summary):
                notes.append(f"MEANING_CONFLICT with {item.knowledge_id} "
                             f"({item.title}: {item.summary[:100]})")
            candidate_community = _norm(candidate.scope.community) if candidate.scope else ""
            if candidate_community and candidate_community not in {_norm(tag) for tag in item.tags}:
                notes.append(f"SCOPE_CONFLICT with {item.knowledge_id}: candidate is scoped to "
                             f"{candidate_community!r}, existing item is not")
            effective = candidate.scope.effective_from if candidate.scope else None
            if effective is not None and item.effective_from is not None \
                    and effective < item.effective_from:
                notes.append(f"TEMPORAL_CONFLICT with {item.knowledge_id}: candidate period "
                             f"starts earlier")
        return tuple(dict.fromkeys(notes))

    # -- write path (administrator only) -----------------------------------
    def approve(self, candidate_id: str, *, admin: str = "administrator",
                edits: dict[str, Any] | None = None, supersede: bool = False,
                note: str = "") -> KnowledgeItem:
        candidate = self._candidates.get(candidate_id)
        if candidate is None:
            raise KeyError(f"Unknown candidate {candidate_id!r}")
        if candidate.status in ("REJECTED", "SUPERSEDED"):
            raise GovernanceError(f"candidate {candidate_id!r} is {candidate.status}")
        if edits:
            candidate = candidate.model_copy(update=edits)
        conflicts = self.conflicts(candidate)
        if conflicts and not supersede:
            raise GovernanceError(
                "unresolved conflicts; review them or pass supersede: "
                + "; ".join(conflicts))
        item = self._promote(candidate, admin=admin, note=note)
        for conflict in conflicts:
            existing_id = conflict.split()[2] if len(conflict.split()) > 2 else ""
            if supersede and existing_id:
                existing = self._knowledge.store.get_item(existing_id)
                if existing is not None:
                    self._knowledge.store.upsert_item(existing.model_copy(
                        update={"status": "SUPERSEDED"}))
        self._candidates.update(candidate.model_copy(update={
            "status": "ACTIVE", "conflict_notes": conflicts,
            "review_notes": "; ".join(filter(None, [candidate.review_notes, note]))}))
        return item

    def edit_approve(self, candidate_id: str, edits: dict[str, Any], *,
                     admin: str = "administrator", note: str = "",
                     supersede: bool = False) -> KnowledgeItem:
        return self.approve(candidate_id, admin=admin, edits=edits, note=note,
                            supersede=supersede)

    def mark_under_review(self, candidate_id: str) -> CandidateKnowledge:
        candidate = self._require(candidate_id)
        return self._candidates.update(candidate.model_copy(update={"status": "UNDER_REVIEW"}))

    def reject(self, candidate_id: str, *, reason: str = "",
               admin: str = "administrator") -> CandidateKnowledge:
        candidate = self._require(candidate_id)
        return self._candidates.update(candidate.model_copy(
            update={"status": "REJECTED",
                    "review_notes": "; ".join(filter(None, [candidate.review_notes,
                                                            f"rejected by {admin}: {reason}"]))}))

    def retire(self, candidate_id: str, *, reason: str = "") -> CandidateKnowledge:
        candidate = self._require(candidate_id)
        item = self._knowledge.store.get_item(f"CAND-{candidate_id}")
        if item is not None:
            self._knowledge.store.upsert_item(item.model_copy(update={"status": "SUPERSEDED"}))
        return self._candidates.update(candidate.model_copy(
            update={"status": "RETIRED", "review_notes": reason}))

    def _require(self, candidate_id: str) -> CandidateKnowledge:
        candidate = self._candidates.get(candidate_id)
        if candidate is None:
            raise KeyError(f"Unknown candidate {candidate_id!r}")
        return candidate

    def _promote(self, candidate: CandidateKnowledge, *, admin: str,
                 note: str) -> KnowledgeItem:
        scope = candidate.scope or CandidateScope()
        authority = scope.authority if scope.authority in AUTHORITY_RANK else "COMMUNITY"
        knowledge_type = _CATEGORY_TO_TYPE.get(
            candidate.effective_category.upper(), "TERM")
        item = KnowledgeItem(
            knowledge_id=f"CAND-{candidate.candidate_id}",
            canonical_key=candidate.surface,
            knowledge_type=knowledge_type,
            title=candidate.surface,
            aliases=tuple(dict.fromkeys((candidate.surface,))),
            language=candidate.language if candidate.language in ("en", "zh", "ja") else "en",
            summary=candidate.meaning or candidate.context,
            source_refs=tuple(candidate.provenance),
            source_authority=authority,
            effective_from=scope.effective_from,
            effective_to=scope.effective_to,
            tags=tuple(item for item in (scope.domain, scope.community) if item),
            verification_status="VERIFIED",
            status="ACTIVE")
        return self._knowledge.store.upsert_item(item)
