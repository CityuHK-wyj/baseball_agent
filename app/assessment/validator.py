"""Deterministic validation: the factual baseline for every assessment.

Produces hard failures (never overridable by the Judge) and soft signals (facts a
contextual Judge may reinterpret for a specific requirement).
"""

import json

from app.assessment.adequacy import qualification_signal, sample_adequacy_signal
from app.models.artifacts import Artifact, DeterministicResult, HardFailure, SoftSignal
from app.models.contracts import ArtifactRequirement, Constraint, Entity, LeagueStateSnapshot


def _entity_key(entity: Entity) -> tuple[str, str, str]:
    return (entity.namespace, entity.entity_type, entity.identifier)


def _constraint_key(constraint: Constraint) -> str:
    """Stable, hashable identity for typed constraints, including tuple-valued ones."""
    return json.dumps(constraint.model_dump(mode="json"), sort_keys=True)


def _coverage_fraction(required, observed) -> float:
    overlap_start = max(required.start, observed.start)
    overlap_end = min(required.end, observed.end)
    if overlap_end < overlap_start:
        return 0.0
    required_days = (required.end - required.start).days + 1
    observed_days = (overlap_end - overlap_start).days + 1
    return observed_days / required_days


def validate_artifact(artifact: Artifact, requirement: ArtifactRequirement,
                      league_state: LeagueStateSnapshot | None = None) -> DeterministicResult:
    """Compare one Artifact against one Requirement on program-verifiable facts only."""
    hard: list[HardFailure] = []
    soft: list[SoftSignal] = []
    need, have = requirement.descriptor, artifact.descriptor

    if artifact.integrity != "OK":
        return DeterministicResult(hard_failures=(HardFailure(
            code="INTEGRITY_FAILURE", detail=f"Artifact integrity is {artifact.integrity}"),))

    if have.artifact_type != need.artifact_type:
        hard.append(HardFailure(
            code="TYPE_MISMATCH",
            detail=f"Requirement wants {need.artifact_type}, artifact is {have.artifact_type}"))

    required_entities = {_entity_key(entity) for entity in need.entities}
    observed_entities = {_entity_key(entity) for entity in have.entities}
    if not required_entities.issubset(observed_entities):
        hard.append(HardFailure(
            code="ENTITY_MISMATCH",
            detail=f"Artifact does not carry required entities {sorted(required_entities - observed_entities)}"))

    missing_keys = set(need.data_keys) - set(have.data_keys)
    if missing_keys:
        hard.append(HardFailure(
            code="MISSING_DATA_KEY", detail=f"Missing required data keys {sorted(missing_keys)}"))

    missing_optional = set(need.optional_data_keys) - set(have.data_keys)
    for key in sorted(missing_optional):
        soft.append(SoftSignal(code="OPTIONAL_KEY_MISSING", detail=f"Optional data key {key} is absent"))

    required_constraints = {_constraint_key(item) for item in need.constraints}
    observed_constraints = {_constraint_key(item) for item in have.constraints}
    unapplied = required_constraints - observed_constraints
    if unapplied:
        hard.append(HardFailure(
            code="CONSTRAINT_MISMATCH",
            detail=f"Artifact does not declare {len(unapplied)} required constraint(s)"))

    if need.time_range is not None:
        observed_range = artifact.observed_time_range
        if observed_range is None:
            soft.append(SoftSignal(
                code="PARTIAL_TIME_COVERAGE", severity="MODERATE",
                detail="Artifact reports no observed time coverage"))
        else:
            fraction = _coverage_fraction(need.time_range, observed_range)
            if fraction == 0.0:
                hard.append(HardFailure(
                    code="TIME_RANGE_MISMATCH",
                    detail=f"Observed range {observed_range.start}..{observed_range.end} misses the required range"))
            elif fraction < 1.0:
                severity = "MINOR" if fraction >= 0.8 else "MODERATE" if fraction >= 0.5 else "MAJOR"
                soft.append(SoftSignal(
                    code="PARTIAL_TIME_COVERAGE", severity=severity,
                    detail=f"Observed coverage is {fraction:.0%} of the required range"))

    if artifact.row_count == 0:
        soft.append(SoftSignal(code="ZERO_ROWS", severity="MAJOR", detail="Artifact contains no rows"))

    if requirement.sample_adequacy_rule is not None:
        signal = sample_adequacy_signal(requirement.sample_adequacy_rule, artifact.row_count)
        if signal is not None:
            soft.append(signal)
    elif artifact.row_count is not None and requirement.min_row_count is not None:
        if artifact.row_count < requirement.min_row_count:
            severity = "MODERATE" if artifact.row_count * 2 >= requirement.min_row_count else "MAJOR"
            soft.append(SoftSignal(
                code="LOW_SAMPLE", severity=severity,
                detail=f"{artifact.row_count} rows below the {requirement.min_row_count} row floor"))

    qualification = qualification_signal(requirement.qualification_rule, league_state)
    if qualification is not None:
        soft.append(qualification)

    return DeterministicResult(hard_failures=tuple(hard), soft_signals=tuple(soft))
