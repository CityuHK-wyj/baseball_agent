"""Validated domain definitions and separate runtime projections."""

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator


Name = Annotated[str, Field(min_length=1, pattern=r"\S")]


class Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)


class Entity(Contract):
    namespace: Name
    entity_type: Literal["PLAYER", "TEAM", "LEAGUE"]
    identifier: Name


class TimeRange(Contract):
    start: date
    end: date

    @model_validator(mode="after")
    def ordered(self):
        if self.end < self.start:
            raise ValueError("Time range ends before it starts")
        return self


class NumericConstraint(Contract):
    kind: Literal["NUMERIC"] = "NUMERIC"
    key: Name
    operator: Literal["EQ", "GT", "GTE", "LT", "LTE"]
    value: FiniteFloat
    unit: Name
    origin: Literal["USER_CONFIRMED", "SYSTEM_INFERRED"] = "USER_CONFIRMED"


class CategoryConstraint(Contract):
    kind: Literal["CATEGORY"] = "CATEGORY"
    key: Name
    values: tuple[Name, ...] = Field(min_length=1)
    origin: Literal["USER_CONFIRMED", "SYSTEM_INFERRED"] = "USER_CONFIRMED"


Constraint = Annotated[NumericConstraint | CategoryConstraint, Field(discriminator="kind")]


class ArtifactDescriptor(Contract):
    artifact_type: Literal["TABLE", "EVIDENCE", "FEATURE"]
    entities: tuple[Entity, ...] = ()
    data_keys: tuple[Name, ...] = Field(min_length=1)
    optional_data_keys: tuple[Name, ...] = ()
    time_range: TimeRange | None = None
    constraints: tuple[Constraint, ...] = ()
    granularity: Name
    population_scope: Name


class AnalysisObjective(Contract):
    objective_id: Name
    raw_query: Name
    description: Name
    objective_type: Literal["PERFORMANCE", "INJURY", "VALUE", "STRATEGY", "CONTEXT"] = "PERFORMANCE"
    subtype: str = ""
    constraints: tuple[Constraint, ...] = ()


class ArtifactRequirement(Contract):
    requirement_id: Name
    objective_ref: Name
    description: Name
    descriptor: ArtifactDescriptor
    origin: Literal["INITIAL", "PLANNER_ADDED"] = "INITIAL"
    base_criticality: Literal["CORE", "OPTIONAL"] = "CORE"
    parent_ref: Name | None = None
    # Contextual framing, not a property of any Artifact: the Judge uses it to
    # reinterpret soft signals for this requirement.
    evidence_purpose: Literal["EXISTENCE", "DESCRIPTIVE", "INFERENTIAL"] = "DESCRIPTIVE"
    min_row_count: int | None = Field(default=None, ge=0)


class RequirementState(Contract):
    requirement_ref: Name
    status: Literal["PENDING", "PARTIAL", "SATISFIED", "UNSATISFIED"] = "PENDING"
    assessment_refs: tuple[Name, ...] = ()
    version: int = Field(default=0, ge=0)


class ObjectiveState(Contract):
    objective_ref: Name
    status: Literal["PENDING", "IN_PROGRESS", "COMPLETE", "LIMITED", "FAILED"] = "PENDING"
    requirement_refs: tuple[Name, ...]
    version: int = Field(default=0, ge=0)
