"""Candidate knowledge governance.

The runtime may discover a new alias/meaning (for example a community nickname found by
web research). It must NOT write it into authoritative Shared Knowledge. Instead it
submits a :class:`CandidateKnowledge` record with provenance and a real lifecycle.
Promotion to ACTIVE knowledge happens only through an explicit administrator action
(``app.knowledge.governance``).

``Collected != Approved`` and ``Evidence usable now != permanent system knowledge``.
"""

from datetime import date, datetime
from typing import Literal
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from app.models.artifacts import ArtifactContract, utcnow

# Real governance lifecycle. Runtime code may only create CANDIDATE records.
CandidateStatus = Literal[
    "CANDIDATE", "UNDER_REVIEW", "APPROVED", "ACTIVE", "REJECTED", "RETIRED", "SUPERSEDED"]

# Explicit knowledge categories so a single alias table is not used for everything.
KnowledgeCategory = Literal[
    "CANONICAL_FACT", "ENTITY_ALIAS", "DEFINITION", "HISTORICAL_EVENT", "RULE",
    "COMMUNITY_REFERENCE", "COMMUNITY_OPINION", "SCOUTING_NOTE", "TACTICAL_CONCEPT",
    "SOURCE_INTERPRETATION", "CONTEXT_REFERENCE", "ALIAS", "TERM",
]


class CandidateScope(ArtifactContract):
    """Scope in which a candidate meaning applies (never globally by default)."""

    domain: str = ""
    language: str = "und"
    locale: str = ""
    community: str = ""
    effective_from: date | None = None
    effective_to: date | None = None
    authority: str = "UNVERIFIED"
    source_refs: tuple[str, ...] = ()


class CandidateKnowledge(ArtifactContract):
    candidate_id: str
    knowledge_type: str = "ALIAS"
    proposed_type: str = ""
    surface: str
    meaning: str = ""
    language: str = ""
    context: str = ""
    confidence: str = "LOW"
    discovered_from_query: str = ""
    originating_run: str = ""
    evidence: tuple[str, ...] = ()
    evidence_spans: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()
    scope: CandidateScope | None = None
    reason_reusable: str = ""
    conflict_notes: tuple[str, ...] = ()
    review_notes: str = ""
    superseded_by: str = ""
    status: CandidateStatus = "CANDIDATE"
    created_at: datetime = utcnow()

    @property
    def effective_category(self) -> str:
        return self.proposed_type or self.knowledge_type or "CONTEXT_REFERENCE"


class CandidateKnowledgeStore:
    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._connection = sqlite3.connect(self._path)
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS candidate_knowledge ("
            "candidate_id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL)")
        self._connection.commit()

    def submit(self, candidate: CandidateKnowledge) -> CandidateKnowledge:
        self._connection.execute(
            "INSERT OR REPLACE INTO candidate_knowledge (candidate_id, payload, created_at) "
            "VALUES (?, ?, ?)",
            (candidate.candidate_id, candidate.model_dump_json(),
             candidate.created_at.isoformat()))
        self._connection.commit()
        return candidate

    def get(self, candidate_id: str) -> CandidateKnowledge | None:
        row = self._connection.execute(
            "SELECT payload FROM candidate_knowledge WHERE candidate_id = ?",
            (candidate_id,)).fetchone()
        return CandidateKnowledge.model_validate(json.loads(row[0])) if row else None

    def list(self, status: str | None = None) -> tuple[CandidateKnowledge, ...]:
        rows = self._connection.execute(
            "SELECT payload FROM candidate_knowledge ORDER BY created_at DESC").fetchall()
        items = tuple(CandidateKnowledge.model_validate(json.loads(row[0])) for row in rows)
        return tuple(item for item in items if status is None or item.status == status)

    def review(self, candidate_id: str, *, approve: bool) -> CandidateKnowledge:
        """Backward-compatible status change; promotion to ACTIVE is separate."""
        candidate = self.get(candidate_id)
        if candidate is None:
            raise KeyError(f"Unknown candidate {candidate_id!r}")
        updated = candidate.model_copy(
            update={"status": "APPROVED" if approve else "REJECTED"})
        return self.submit(updated)

    def update(self, candidate: CandidateKnowledge) -> CandidateKnowledge:
        return self.submit(candidate)

    def close(self) -> None:
        self._connection.close()


def new_candidate(**fields) -> CandidateKnowledge:
    return CandidateKnowledge(candidate_id=fields.pop("candidate_id", f"candidate-{uuid4().hex}"),
                              **fields)
