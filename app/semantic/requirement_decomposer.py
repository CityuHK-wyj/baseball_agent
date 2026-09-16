"""Requirement Decomposer: turn an Objective into semantic-atomic Initial Requirements.

It answers only "what information is needed?" (D034). It does not choose tools, and it
must not drop a user requirement because a source is inconvenient. The deterministic
implementation is the tested default; an LLM implementation can replace it.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from app.models.artifacts import ArtifactContract
from app.models.contracts import (AnalysisObjective, ArtifactDescriptor, ArtifactRequirement,
                                  CountConstraint, LeagueStateSnapshot, LocationConstraint,
                                  PitchTypeConstraint, QualificationRule, RankingConstraint,
                                  SampleAdequacyRule, TimeRange)
from app.models.metrics import MetricDefinition
from app.models.schema import SchemaTable


class DecompositionContext(ArtifactContract):
    metric_definitions: tuple[MetricDefinition, ...] = ()
    schema_tables: tuple[SchemaTable, ...] = ()
    league_state: LeagueStateSnapshot | None = None


@dataclass(frozen=True)
class _RequirementSpec:
    description: str
    artifact_type: str
    required_keys: tuple[str, ...]
    granularity: str
    purpose: str
    optional_keys: tuple[str, ...] = ()
    criticality: str = "CORE"
    sample_adequacy: SampleAdequacyRule | None = None
    qualification: QualificationRule | None = None


_PERFORMANCE_ADEQUACY = SampleAdequacyRule(min_sample=30, sample_unit="BATTED_BALL",
                                           note="enough batted balls to describe recent contact quality")

_SPECS: dict[str, tuple[_RequirementSpec, ...]] = {
    "PERFORMANCE": (
        _RequirementSpec("recent batted-ball contact quality", "TABLE",
                         ("exit_velocity", "launch_angle"), "batted_ball", "DESCRIPTIVE",
                         optional_keys=("hard_hit_rate", "barrel_rate"),
                         sample_adequacy=_PERFORMANCE_ADEQUACY),
        _RequirementSpec("historical performance baseline", "TABLE", ("game_date",), "game",
                         "DESCRIPTIVE", criticality="OPTIONAL"),
    ),
    "INJURY": (
        _RequirementSpec("injury status evidence", "EVIDENCE", ("injury_status",), "event",
                         "EXISTENCE"),
    ),
    "VALUE": (
        _RequirementSpec("salary / contract evidence", "EVIDENCE", ("salary",), "season",
                         "EXISTENCE"),
    ),
    "STRATEGY": (
        _RequirementSpec("lineup / strategy context", "TABLE", ("lineup_slot",), "game",
                         "DESCRIPTIVE"),
    ),
    "CONTEXT": (
        _RequirementSpec("narrative context evidence", "EVIDENCE", ("news_claim",), "event",
                         "DESCRIPTIVE"),
    ),
}


class RequirementDecomposer(Protocol):
    def decompose(self, objective: AnalysisObjective,
                  context: DecompositionContext | None = None) -> tuple[ArtifactRequirement, ...]: ...


def _is_analytics_objective(objective: AnalysisObjective) -> bool:
    return any(isinstance(item, (CountConstraint, PitchTypeConstraint, LocationConstraint,
                                 RankingConstraint)) for item in objective.constraints)


class RuleBasedRequirementDecomposer:
    def __init__(self, id_factory: Callable[[str], str] | None = None) -> None:
        self._id_factory = id_factory or (lambda prefix: f"{prefix}-{abs(hash(prefix))}")

    def decompose(self, objective: AnalysisObjective,
                  context: DecompositionContext | None = None) -> tuple[ArtifactRequirement, ...]:
        if _is_analytics_objective(objective):
            return self._analytics_requirements(objective)
        specs = _SPECS.get(objective.objective_type, _SPECS["PERFORMANCE"])
        if objective.subtype == "knowledge":
            specs = (_RequirementSpec(objective.raw_query, "EVIDENCE", ("knowledge_statement",),
                                      "reference", "EXISTENCE"),)
        population_scope = "player" if any(
            entity.entity_type == "PLAYER" for entity in objective.entities) else "league"
        requirements: list[ArtifactRequirement] = []
        date_constraints = [c for c in objective.constraints if c.key == "date_range"]
        if any(c.kind != "CATEGORY" or len(c.values) != 2 for c in date_constraints):
            raise ValueError("Date range requires exactly two ISO dates")
        windows = [TimeRange(start=c.values[0], end=c.values[1]) for c in date_constraints]
        if any(w != windows[0] for w in windows):
            raise ValueError("Conflicting date ranges require clarification")
        time_range = windows[0] if windows else None
        for spec in specs:
            descriptor = ArtifactDescriptor(
                artifact_type=spec.artifact_type, entities=objective.entities,
                data_keys=spec.required_keys, optional_data_keys=spec.optional_keys,
                constraints=tuple(c for c in objective.constraints
                                  if spec.criticality == "CORE" or c.key != "date_range"),
                granularity=spec.granularity,
                time_range=time_range if spec.criticality == "CORE" else None,
                population_scope=population_scope)
            requirements.append(ArtifactRequirement(
                requirement_id=self._id_factory("requirement"), objective_ref=objective.objective_id,
                description=spec.description, descriptor=descriptor, origin="INITIAL",
                base_criticality=spec.criticality, evidence_purpose=spec.purpose,
                sample_adequacy_rule=spec.sample_adequacy, qualification_rule=spec.qualification))
        return tuple(requirements)

    def _analytics_requirements(self, objective: AnalysisObjective) -> tuple[ArtifactRequirement, ...]:
        """One semantic-atomic ranking requirement over filtered Statcast pitches.

        The output keys are semantic (exit velocity plus the batter identity); the filter
        concepts stay typed constraints. Physical columns are resolved later by the
        adapter, never by the Planner or this decomposer.
        """
        ranking = next((item for item in objective.constraints if isinstance(item, RankingConstraint)), None)
        location = next((item for item in objective.constraints if isinstance(item, LocationConstraint)), None)
        pitch_type = next((item for item in objective.constraints if isinstance(item, PitchTypeConstraint)), None)
        count = next((item for item in objective.constraints if isinstance(item, CountConstraint)), None)

        parts = ["ranked exit velocity"]
        if count is not None:
            parts.append(f"with {count.strikes} strike(s)")
        if pitch_type is not None:
            parts.append(f"against {pitch_type.family}")
        if location is not None:
            parts.append(f"in {location.definition.casefold()}")
        description = " ".join(parts)

        date_constraints = [c for c in objective.constraints if c.key == "date_range"]
        windows = [TimeRange(start=c.values[0], end=c.values[1]) for c in date_constraints
                   if c.kind == "CATEGORY" and len(c.values) == 2]
        if any(w != windows[0] for w in windows):
            raise ValueError("Conflicting date ranges require clarification")
        time_range = windows[0] if windows else None
        population_scope = "player" if any(
            entity.entity_type == "PLAYER" for entity in objective.entities) else "league"
        descriptor = ArtifactDescriptor(
            artifact_type="TABLE",
            entities=objective.entities,
            data_keys=("exit_velocity", "batter"),
            constraints=objective.constraints,
            granularity="player_rank",
            time_range=time_range,
            population_scope=population_scope)
        requirement = ArtifactRequirement(
            requirement_id=self._id_factory("requirement"), objective_ref=objective.objective_id,
            description=description, descriptor=descriptor, origin="INITIAL",
            base_criticality="CORE", evidence_purpose="DESCRIPTIVE",
            min_row_count=ranking.limit if ranking is not None else None)
        return (requirement,)
