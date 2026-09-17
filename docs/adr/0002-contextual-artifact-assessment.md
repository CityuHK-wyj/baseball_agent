# Contextual Artifact assessment with non-overridable hard failures

Status: accepted

## Context

Blog decisions D048 and D058–D060 require that artifact usability is contextual
(bound to an Artifact *and* a Requirement), not an absolute score on the artifact.
D060 also requires that deterministic facts form a baseline and that a Judge may
reinterpret soft signals but must never override a hard failure. The runtime needs
a concrete, testable contract for this before planning can consume assessments.

## Decision

- `Artifact` is immutable and carries no quality field.
- `validate_artifact(artifact, requirement)` produces a `DeterministicResult` of
  hard failures (integrity, type, entity, required key, constraint, time-range
  mismatch) and soft signals (zero rows, low sample, partial coverage, stale,
  optional key, qualification).
- `RuleBasedJudge` maps deterministic facts plus `ArtifactRequirement.evidence_purpose`
  (EXISTENCE / DESCRIPTIVE / INFERENTIAL) to a `JudgeResult` level.
- `ArtifactAssessment` binds `artifact_ref` + `requirement_ref` (+ optional
  `objective_ref`) and stores deterministic result, judge result, final level,
  short summary, usable-for and limitations.
- A Pydantic validator rejects any `ArtifactAssessment` whose deterministic result
  has hard failures but whose final level is not `REJECT`. The service also coerces
  the level, so the veto holds even if a future Judge misbehaves.

## Alternatives

- Absolute quality score on `Artifact`: rejected by D048; it cannot express that the
  same artifact is strong for one requirement and weak for another.
- Let the Judge own all validation: rejected; it makes program-verifiable facts
  negotiable and hides integrity/entity errors.
- LLM Judge as the default: deferred. The seam is a `Judge` Protocol; a deterministic
  rule Judge is used first so behavior is reproducible and testable.

## Consequences

- `evidence_purpose` and `min_row_count` were added to `ArtifactRequirement` as
  additive, defaulted fields. The Judge needs requirement context to be contextual.
- `ArtifactDescriptor` gained a defaulted `optional_data_keys` field.
- Assessment summaries are short and deterministic so the Planner consumes a
  projection instead of re-running Judge work.
- Hard-failure codes are a closed set; adding one is an explicit schema change.
