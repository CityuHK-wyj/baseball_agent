"""Evidence extraction: unstructured web text -> structured Evidence.

The Router only decides *where* to look; this stage decides what a document actually
claims. The deterministic extractor is the tested default; an LLM extractor can replace it
behind the same Protocol. Extracted evidence still enters the Artifact system via
``evidence_to_artifact`` so it is validated and assessed like any other product.
"""

import re
from collections.abc import Callable
from typing import Protocol

from app.models.artifacts import Artifact, ArtifactDescriptor, Provenance
from app.models.evidence import Evidence, EvidenceClaim, RawWebResult

_SENTENCE = re.compile(r"[^.!?]+[.!?]?")
_SIGNAL = re.compile(r"(?i)\b(injur\w*|il\b|surgery|salary|contract|signed|trade\w*|extension|"
                     r"wrc\+|era\b|home run|homer|strain|sprain|out for)\b")
_NUMBER = re.compile(r"\d")


class EvidenceExtractor(Protocol):
    def extract(self, raw: RawWebResult) -> Evidence: ...


class RuleBasedEvidenceExtractor:
    """Deterministic sentence-level extractor. Keeps the sentence as supporting context."""

    def __init__(self, id_factory: Callable[[str], str] | None = None,
                 min_sentence_length: int = 20, max_claim_length: int = 300) -> None:
        self._id_factory = id_factory or (lambda prefix: f"{prefix}-{abs(hash(prefix))}")
        self._min_length = min_sentence_length
        self._max_claim = max_claim_length

    def extract(self, raw: RawWebResult) -> Evidence:
        claims: list[EvidenceClaim] = []
        for sentence in _SENTENCE.findall(raw.text):
            candidate = sentence.strip()
            if len(candidate) < self._min_length:
                continue
            if not (_SIGNAL.search(candidate) or _NUMBER.search(candidate)):
                continue
            claims.append(EvidenceClaim(claim=candidate[:self._max_claim], support=candidate,
                                        claim_time=_first_year(candidate)))
        return Evidence(
            evidence_id=self._id_factory("evidence"), raw_ref=raw.result_id, url=raw.url,
            title=raw.title, claims=tuple(claims),
            provenance=Provenance(source=raw.source, source_kind="WEB", reference=raw.url,
                                  retrieved_at=raw.retrieved_at))


def _first_year(text: str) -> str:
    match = re.search(r"\b(19|20)\d{2}\b", text)
    return match.group(0) if match else ""


def evidence_to_artifact(evidence: Evidence, raw_artifact_id: str,
                         descriptor: ArtifactDescriptor | None = None) -> Artifact:
    """Wrap structured evidence as a unified EVIDENCE artifact with lineage to the raw result.

    Structured claims stay structured; the supporting text is retained as unstructured
    ``text_content`` so the Judge and Response can use it without forcing web material
    into an analytics schema.
    """
    descriptor = descriptor or ArtifactDescriptor(artifact_type="EVIDENCE", data_keys=("claim",),
                                                  granularity="claim", population_scope="web")
    text = "\n".join(claim.support or claim.claim for claim in evidence.claims)
    return Artifact(
        artifact_id=evidence.evidence_id, descriptor=descriptor,
        payload_ref=f"evidence://{evidence.evidence_id}",
        provenance=evidence.provenance, lineage=(raw_artifact_id,),
        row_count=len(evidence.claims), text_content=text[:20000])
