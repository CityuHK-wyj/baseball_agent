"""Artifact contracts: what exists, and its contextual usability.

An ``Artifact`` carries no absolute quality. Usability is always expressed as an
``ArtifactAssessment`` bound to one ``ArtifactRequirement`` and ``AnalysisObjective``.
"""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.contracts import ArtifactDescriptor, Name, TimeRange
from app.models.understanding import MAX_FREE_TEXT


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ArtifactContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


QualityLevel = Literal["STRONG", "ACCEPTABLE", "WEAK", "REJECT"]


class Provenance(ArtifactContract):
    source: Name
    source_kind: Literal["POSTGRES", "PARQUET", "WEB", "FEATURE", "SYNTHETIC"]
    reference: str = ""
    retrieved_at: datetime = Field(default_factory=utcnow)


class Artifact(ArtifactContract):
    """An immutable produced result. Payload lives outside the state graph.

    ``text_content`` carries unstructured evidence (for example extracted web text)
    alongside the structured payload reference. A web artifact is not forced into a
    Statcast schema; the Judge decides whether its text satisfies a Requirement.
    """

    artifact_id: Name
    descriptor: ArtifactDescriptor
    payload_ref: Name
    provenance: Provenance
    lineage: tuple[Name, ...] = ()
    row_count: int | None = Field(default=None, ge=0)
    observed_time_range: TimeRange | None = None
    integrity: Literal["OK", "MALFORMED", "CORRUPTED"] = "OK"
    text_content: str = Field(default="", max_length=MAX_FREE_TEXT)
    created_at: datetime = Field(default_factory=utcnow)


class HardFailure(ArtifactContract):
    code: Literal[
        "INTEGRITY_FAILURE",
        "TYPE_MISMATCH",
        "ENTITY_MISMATCH",
        "MISSING_DATA_KEY",
        "CONSTRAINT_MISMATCH",
        "TIME_RANGE_MISMATCH",
        "POLICY_VIOLATION",
    ]
    detail: Name


class SoftSignal(ArtifactContract):
    code: Literal[
        "ZERO_ROWS",
        "LOW_SAMPLE",
        "PARTIAL_TIME_COVERAGE",
        "STALE_DATA",
        "OPTIONAL_KEY_MISSING",
        "QUALIFICATION_PARTIAL",
    ]
    detail: Name
    severity: Literal["MINOR", "MODERATE", "MAJOR"] = "MINOR"


class DeterministicResult(ArtifactContract):
    """Facts only. No final usability judgment lives here."""

    hard_failures: tuple[HardFailure, ...] = ()
    soft_signals: tuple[SoftSignal, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.hard_failures


class JudgeResult(ArtifactContract):
    """Contextual interpretation of soft signals for one requirement."""

    level: QualityLevel
    rationale: Name
    reinterpreted_signals: tuple[Name, ...] = ()


class ArtifactAssessment(ArtifactContract):
    assessment_id: Name
    artifact_ref: Name
    requirement_ref: Name
    objective_ref: Name | None = None
    deterministic_result: DeterministicResult
    judge_result: JudgeResult | None = None
    final_level: QualityLevel
    assessment_summary: Name
    usable_for: tuple[Name, ...] = ()
    limitations: tuple[Name, ...] = ()
    assessed_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def hard_failure_cannot_be_overridden(self):
        if self.deterministic_result.hard_failures and self.final_level != "REJECT":
            raise ValueError("A hard deterministic failure forces REJECT")
        return self

    @property
    def accepted(self) -> bool:
        return self.final_level in ("STRONG", "ACCEPTABLE")


class ArtifactIndexEntry(ArtifactContract):
    """Bounded Planner-visible index; never the payload itself."""

    artifact_ref: Name
    artifact_type: Literal["TABLE", "EVIDENCE", "FEATURE"]
    summary: Name
    row_count: int | None = None


class AssessmentSummary(ArtifactContract):
    """Short Planner-visible view of one contextual assessment."""

    assessment_ref: Name
    artifact_ref: Name
    requirement_ref: Name
    final_level: QualityLevel
    summary: Name
