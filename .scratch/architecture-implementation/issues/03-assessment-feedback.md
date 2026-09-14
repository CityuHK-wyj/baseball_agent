# Artifact assessment and state feedback

Status: DONE
Blocked by: None

Extend the shared descriptor to immutable Artifact metadata and payload references, provenance and lineage. Implement deterministic matching with hard/soft facts and contextual Judge resolution. A hard failure cannot be overridden. State updates consume assessment refs, not new semantic judgments. Completion checks core Initial Requirements only and preserves optional gaps. Tests must show the same artifact can be acceptable for existence evidence and weak for ability inference.

## Comments

- Delivered in ADR 0002: `app/models/artifacts.py`, `app/assessment/{validator,judge,service}.py`,
  `app/agent/registry.py`, `app/state/services.py`.
- Tests: `tests/test_artifacts.py`, `tests/test_state.py`. The existence-vs-inference case
  is explicit (`test_same_artifact_is_acceptable_for_existence_and_weak_for_inference`).
- Hard-failure veto enforced both in the `ArtifactAssessment` validator and the service.
- Optional gaps retained on COMPLETE; only core Initial Requirements gate completion.
