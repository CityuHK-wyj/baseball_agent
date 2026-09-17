"""Bounded semantic-review artifact.

This is the only thing the dual semantic layer persists for audit: the two independent
candidates, the structured comparison and the reconciled canonical candidate. It never
contains hidden model reasoning, raw prompts, SQL or credentials.
"""

from typing import Literal

from pydantic import Field

from app.models.artifacts import ArtifactContract
from app.models.contracts import Name
from app.models.semantic_candidate import SemanticCandidate

AgreementStatus = Literal[
    "AGREE",
    "NON_MATERIAL_DIFFERENCES",
    "MATERIAL_DISAGREEMENT",
    "AMBIGUOUS",
    "EXTRACTOR_UNAVAILABLE",
    "REVIEWER_UNAVAILABLE",
    "BOTH_UNAVAILABLE",
]

ReviewOutcome = Literal[
    "DUAL_REVIEW",
    "SINGLE_REVIEW",
    "DETERMINISTIC_ONLY",
    "CACHE_REUSE",
]


class MaterialDifference(ArtifactContract):
    """One structured, non-free-text difference between two candidates or an anchor."""

    dimension: Name
    code: Name
    detail: str = ""


class ModelCallMetrics(ArtifactContract):
    """Bounded per-call metrics. Never secrets, prompts or chain-of-thought."""

    role: Literal["extractor", "reviewer"]
    model: str = ""
    latency_ms: int = Field(default=0, ge=0)
    ok: bool = True
    failure: str = ""
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)


class SemanticReviewResult(ArtifactContract):
    """Structured outcome of one semantic review (independent proposal + reconciliation)."""

    raw_query_hash: str = ""
    outcome: ReviewOutcome = "DETERMINISTIC_ONLY"
    agreement_status: AgreementStatus = "AGREE"
    extractor_candidate: SemanticCandidate | None = None
    reviewer_candidate: SemanticCandidate | None = None
    canonical_candidate: SemanticCandidate | None = None
    material_differences: tuple[MaterialDifference, ...] = ()
    ambiguities: tuple[Name, ...] = ()
    clarification_reason: str = ""
    calls: tuple[ModelCallMetrics, ...] = ()
    extractor_model: str = ""
    reviewer_model: str = ""
    reviewed_at: str = ""
    notes: tuple[str, ...] = ()

    @property
    def safe_to_execute(self) -> bool:
        return (self.agreement_status in ("AGREE", "NON_MATERIAL_DIFFERENCES")
                and not self.material_differences and not self.clarification_reason)
