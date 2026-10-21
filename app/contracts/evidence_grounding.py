"""WSA-09 semantic grounding receipts and bounded conformance decisions.

An authority-increasing semantic interpretation (an LLM proposing ENTAILS, a stronger
assertion, a high-authority source role or a current temporal reading) must identify the
acquired source material on which that interpretation is based. The receipt makes the
interpretation grounded, inspectable and auditable; it never proves semantic correctness.

Deterministic code validates only what it can prove exactly (does the quoted text exist in
the supplied span/context/metadata, does the locator resolve, is the basis kind a genuine
basis rather than an in-text self-description). Whether an arbitrary natural-language
sentence *means* what the assessor claims remains open-language interpretation.
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field
from app.contracts.immutability import GovernableModel as BaseModel


class GroundingField(str, Enum):
    RELATION = "RELATION"
    ASSERTION_STRENGTH = "ASSERTION_STRENGTH"
    TEMPORAL_READING = "TEMPORAL_READING"
    SOURCE_ROLE = "SOURCE_ROLE"


class GroundingLocator(str, Enum):
    SPAN = "SPAN"
    CONTEXT_BEFORE = "CONTEXT_BEFORE"
    CONTEXT_AFTER = "CONTEXT_AFTER"
    DOCUMENT_METADATA = "DOCUMENT_METADATA"
    STRUCTURED_PROVIDER = "STRUCTURED_PROVIDER"


class GroundingBasisKind(str, Enum):
    SOURCE_QUOTE = "SOURCE_QUOTE"                      # exact wording in the span
    SURROUNDING_CONTEXT = "SURROUNDING_CONTEXT"        # exact wording in bounded context
    DOCUMENT_METADATA = "DOCUMENT_METADATA"            # url/domain/provider/title/byline metadata
    STRUCTURED_PROVIDER = "STRUCTURED_PROVIDER"        # provider-native structured record
    SELF_DESCRIPTION = "SELF_DESCRIPTION"              # in-text self-declaration: NOT authority


class SemanticGroundingReceipt(BaseModel):
    """One authority-increasing semantic proposal plus its acquired-source basis.

    ``source_quote`` must be an exact excerpt of the material named by ``locator``. It is a
    grounding receipt, not a truth proof and not a new evidence class.
    """

    field: GroundingField
    source_quote: str = ""
    locator: GroundingLocator = GroundingLocator.SPAN
    basis_kind: GroundingBasisKind = GroundingBasisKind.SOURCE_QUOTE
    metadata_key: str = ""
    rationale: str = ""


class SemanticConformanceDecision(BaseModel):
    """Bounded proposal-vs-receipt review result. Never a truth judgment or a second vote."""

    supported: bool = False
    reason: str = ""


class AuthorityEscalation(str, Enum):
    """Why a semantic proposal requires grounded positive support (diagnostic only)."""

    RELATION_ENTAILS = "RELATION_ENTAILS"
    ASSERTION_UPGRADE = "ASSERTION_UPGRADE"
    SOURCE_ROLE_AUTHORITY = "SOURCE_ROLE_AUTHORITY"
    TEMPORAL_CURRENT = "TEMPORAL_CURRENT"


class GroundingValidation(BaseModel):
    """Deterministic validation result over the receipts supplied for one assessment."""

    valid: dict[str, SemanticGroundingReceipt] = Field(default_factory=dict)
    rejected: list[str] = Field(default_factory=list)
    escalations: list[str] = Field(default_factory=list)
