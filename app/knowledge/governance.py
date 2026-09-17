"""Administrator-governed promotion of candidate knowledge.

READ PATH : planner / semantic / resolver -> KnowledgeStore -> ACTIVE approved knowledge.
WRITE PATH: runtime discovery -> CandidateKnowledge -> Review Queue -> administrator
            review -> APPROVED/ACTIVE KnowledgeStore.

Runtime agents never write ACTIVE knowledge directly. A confidence threshold is not
approval. This service is the only path that creates ACTIVE knowledge from a candidate,
and it surfaces conflicts instead of overwriting silently.

v0.4 corrections:

* conflicts are structured records with exact IDs (never IDs parsed from human text);
* an unavailable conflict check is an explicit failure, never "no conflict";
* promotion preserves the original category, scope, language/locale/community,
  source/evidence spans, originating run and reviewer;
* promotion is idempotent and recoverable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.knowledge.candidates import (CandidateKnowledge, CandidateKnowledgeStore,
                                      CandidateScope)
from app.models.knowledge import AUTHORITY_RANK, KnowledgeItem

# Candidate categories -> the stable Shared Knowledge vocabulary. The original category is
# always preserved in the promoted item; this map only chooses the retrieval type.
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


class ConflictCheckUnavailable(GovernanceError):
    """The conflict check could not run. It must never be treated as "no conflict"."""


@dataclass(frozen=True)
class ConflictRecord:
    kind: str  # MEANING_CONFLICT / SCOPE_CONFLICT / TEMPORAL_CONFLICT / ALIAS_TARGET_CONFLICT
    existing_id: str
    detail: str
    existing_scope: str = ""
    candidate_scope: str = ""
    resolution: str = "unresolved"


@dataclass(frozen=True)
class ConflictCheck:
    available: bool
    records: tuple[ConflictRecord, ...] = field(default_factory=tuple)
    error: str = ""


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
        return tuple(match.item for match in self._knowledge.search(
            surface, max_items=8, statuses=("ACTIVE",)))

    # -- conflicts ---------------------------------------------------------
    def check_conflicts(self, candidate: CandidateKnowledge) -> ConflictCheck:
        try:
            matches = self.active_matches(candidate.surface)
        except Exception as error:  # noqa: BLE001 - fail closed, never "no conflict"
            return ConflictCheck(available=False, error=f"{type(error).__name__}: {error}")
        records: list[ConflictRecord] = []
        candidate_community = _norm(candidate.scope.community) if candidate.scope else ""
        candidate_locale = _norm(candidate.scope.locale) if candidate.scope else ""
        for item in matches:
            names = {_norm(name) for name in item.names()}
            if _norm(candidate.surface) not in names:
                continue
            item_community = {_norm(tag) for tag in item.tags}
            item_scope = ",".join(sorted(item_community))
            # Scoped meanings may legitimately coexist when their contexts are disjoint.
            if candidate_community and candidate_community not in item_community:
                records.append(ConflictRecord(
                    kind="SCOPE_CONFLICT", existing_id=item.knowledge_id,
                    detail=f"candidate scoped to {candidate_community!r}; existing item "
                           f"scope is {item_scope or '(global)'}",
                    existing_scope=item_scope, candidate_scope=candidate_community))
                continue
            if _norm(candidate.meaning) and _norm(candidate.meaning) != _norm(item.summary):
                records.append(ConflictRecord(
                    kind="MEANING_CONFLICT", existing_id=item.knowledge_id,
                    detail=f"existing {item.knowledge_id} has a different meaning",
                    existing_scope=item_scope, candidate_scope=candidate_community))
            effective = candidate.scope.effective_from if candidate.scope else None
            if effective is not None and item.effective_from is not None \
                    and effective < item.effective_from:
                records.append(ConflictRecord(
                    kind="TEMPORAL_CONFLICT", existing_id=item.knowledge_id,
                    detail=f"candidate period starts {effective} before existing "
                           f"{item.effective_from}",
                    existing_scope=item_scope, candidate_scope=candidate_community))
        return ConflictCheck(available=True, records=tuple(records))

    def conflicts(self, candidate: CandidateKnowledge) -> tuple[ConflictRecord, ...]:
        check = self.check_conflicts(candidate)
        if not check.available:
            raise ConflictCheckUnavailable(check.error)
        return check.records

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
        # Idempotent: an already-promoted candidate returns its active item.
        existing_item = self._knowledge.store.get_item(f"CAND-{candidate.candidate_id}")
        if candidate.status == "ACTIVE" and existing_item is not None:
            return existing_item
        check = self.check_conflicts(candidate)
        if not check.available:
            raise ConflictCheckUnavailable(
                f"conflict check unavailable, refusing to promote: {check.error}")
        if check.records and not supersede:
            raise GovernanceError(
                "unresolved conflicts; review them or pass supersede: "
                + "; ".join(record.detail for record in check.records))
        item = self._promote(candidate, admin=admin, note=note)
        for record in check.records:
            if supersede and record.existing_id:
                existing = self._knowledge.store.get_item(record.existing_id)
                if existing is not None:
                    self._knowledge.store.upsert_item(existing.model_copy(
                        update={"status": "SUPERSEDED"}))
        self._candidates.update(candidate.model_copy(update={
            "status": "ACTIVE",
            "conflict_notes": tuple(record.detail for record in check.records),
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
        original_category = candidate.effective_category
        knowledge_type = _CATEGORY_TO_TYPE.get(original_category.upper(), "TERM")
        canonical_target = ""
        if original_category.upper() in ("ENTITY_ALIAS", "ALIAS"):
            canonical_target = candidate.meaning if _norm(candidate.meaning) else ""
        payload = {
            "candidate_id": candidate.candidate_id,
            "candidate_category": original_category,
            "candidate_scope": scope.model_dump(mode="json"),
            "language": candidate.language or scope.language,
            "locale": scope.locale,
            "community": scope.community,
            "domain": scope.domain,
            "effective_from": scope.effective_from.isoformat() if scope.effective_from else "",
            "effective_to": scope.effective_to.isoformat() if scope.effective_to else "",
            "evidence": list(candidate.evidence),
            "evidence_spans": list(candidate.evidence_spans),
            "provenance": list(candidate.provenance),
            "originating_run": candidate.originating_run,
            "reviewer": admin,
            "review_note": note,
            "verification": candidate.confidence,
            "canonical_target": canonical_target,
            "context": candidate.context,
        }
        item = KnowledgeItem(
            knowledge_id=f"CAND-{candidate.candidate_id}",
            canonical_key=candidate.surface,
            knowledge_type=knowledge_type,
            title=candidate.surface,
            aliases=tuple(dict.fromkeys((candidate.surface,))),
            language=candidate.language if candidate.language in ("en", "zh", "ja") else "en",
            summary=candidate.meaning or candidate.context,
            structured_payload=payload,
            entity_refs=(),
            source_refs=tuple(candidate.provenance),
            source_authority=authority,
            effective_from=scope.effective_from,
            effective_to=scope.effective_to,
            tags=tuple(item for item in (scope.domain, scope.community, scope.locale)
                       if item),
            verification_status="VERIFIED" if candidate.confidence == "HIGH" else "COLLECTED",
            status="ACTIVE")
        return self._knowledge.store.upsert_item(item)
