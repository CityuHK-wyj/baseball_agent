"""Candidate knowledge governance.

The runtime may discover new aliases/meanings (for example a community nickname found by
web research). It must NOT write them into authoritative Shared Knowledge. Instead it
submits a ``CandidateKnowledge`` record with provenance and a CANDIDATE status. Promotion
to ACTIVE knowledge is an explicit administrative action.

``Collected != Approved`` and ``Evidence usable now != permanent system knowledge``.
"""

from datetime import datetime
from typing import Literal
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from app.models.artifacts import ArtifactContract, utcnow

CandidateStatus = Literal["CANDIDATE", "APPROVED", "REJECTED"]


class CandidateKnowledge(ArtifactContract):
    candidate_id: str
    knowledge_type: str = "ALIAS"
    surface: str
    meaning: str = ""
    language: str = ""
    context: str = ""
    confidence: str = "LOW"
    discovered_from_query: str = ""
    evidence: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()
    status: CandidateStatus = "CANDIDATE"
    created_at: datetime = utcnow()


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
            (candidate.candidate_id, candidate.model_dump_json(), candidate.created_at.isoformat()))
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
        candidate = self.get(candidate_id)
        if candidate is None:
            raise KeyError(f"Unknown candidate {candidate_id!r}")
        updated = candidate.model_copy(
            update={"status": "APPROVED" if approve else "REJECTED"})
        return self.submit(updated)

    def close(self) -> None:
        self._connection.close()


def new_candidate(**fields) -> CandidateKnowledge:
    return CandidateKnowledge(candidate_id=fields.pop("candidate_id", f"candidate-{uuid4().hex}"),
                              **fields)
