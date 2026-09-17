"""Web result and evidence contracts.

A raw web result is not approved evidence. ``RawWebResult`` is unstructured input; an
Evidence Extractor turns it into structured ``Evidence`` with claims, supporting context,
time and provenance (D016, P004). Evidence then enters the Artifact system as an EVIDENCE
artifact.
"""

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.models.artifacts import ArtifactContract, Provenance, utcnow
from app.models.contracts import Name


class RawWebResult(ArtifactContract):
    result_id: Name
    url: Name
    title: str = ""
    text: str = ""
    source: Name = "web"
    retrieved_at: datetime = Field(default_factory=utcnow)


class EvidenceClaim(ArtifactContract):
    claim: str
    support: str = ""
    claim_time: str = ""
    entities: tuple[str, ...] = ()


class Evidence(ArtifactContract):
    evidence_id: Name
    raw_ref: Name
    url: str
    title: str = ""
    claims: tuple[EvidenceClaim, ...] = ()
    provenance: Provenance
    extracted_at: datetime = Field(default_factory=utcnow)

    @property
    def is_empty(self) -> bool:
        return not self.claims
