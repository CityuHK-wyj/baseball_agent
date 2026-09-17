"""Durable ToolOutcome taxonomy and safe exception normalization.

Every attempted action has an outcome. No-Artifact failures are first-class ToolOutcomes,
visible to the Planner and the Judge. Unexpected exceptions are centralized into a
``INTERNAL_FAILURE`` outcome instead of escaping the tool boundary (which previously made
the failed step invisible to the trace).
"""

from __future__ import annotations

from app.models.artifact_runtime import RuntimeArtifact, ToolAttempt, ToolOutcomeCode
from app.artifact_runtime.tool_base import ToolOutcome

# Tool-level recovery strings -> the canonical durable taxonomy.
_CODE_MAP: dict[str, ToolOutcomeCode] = {
    "": "SUCCESS",
    "SUCCESS": "SUCCESS",
    "EMPTY_RESULT": "EMPTY_RESULT",
    "MISSING_QUERY": "INPUT_UNRESOLVED",
    "MISSING_TEAM": "INPUT_UNRESOLVED",
    "MISSING_ENTITY_MENTION": "INPUT_UNRESOLVED",
    "MISSING_EVIDENCE_INPUT": "INPUT_UNRESOLVED",
    "MISSING_ENTITY_SET": "INPUT_UNRESOLVED",
    "MISSING_ANALYTICAL_QUERY": "INPUT_UNRESOLVED",
    "INPUT_UNRESOLVED": "INPUT_UNRESOLVED",
    "INPUT_INCOMPATIBLE": "INPUT_INCOMPATIBLE",
    "COMPUTE_INPUT_MISSING": "INPUT_UNRESOLVED",
    "COMPUTE_INPUT_EMPTY": "EMPTY_RESULT",
    "INVALID_IR": "INVALID_IR",
    "UNKNOWN_FIELD": "UNKNOWN_FIELD",
    "UNKNOWN_TABLE": "UNKNOWN_FIELD",
    "UNSUPPORTED_OPERATION": "UNSUPPORTED_CAPABILITY",
    "UNSUPPORTED_COMPUTE_OPERATION": "UNSUPPORTED_CAPABILITY",
    "INVALID_LIMIT": "INVALID_IR",
    "ENTITY_SET_TOO_LARGE": "INPUT_INCOMPATIBLE",
    "INSUFFICIENT_SOURCE_COVERAGE": "COVERAGE_UNAVAILABLE",
    "COVERAGE_UNAVAILABLE": "COVERAGE_UNAVAILABLE",
    "EXECUTOR_UNAVAILABLE": "UNSUPPORTED_CAPABILITY",
    "SOURCE_QUERY_FAILED": "SOURCE_TRANSIENT",
    "ROSTER_UNAVAILABLE": "SOURCE_TRANSIENT",
    "ROSTER_EMPTY": "EMPTY_RESULT",
    "BATTING_STATS_UNAVAILABLE": "SOURCE_TRANSIENT",
    "BATTING_STATS_TOOL_UNAVAILABLE": "UNSUPPORTED_CAPABILITY",
    "INVALID_TIME_RANGE": "INPUT_INCOMPATIBLE",
    "WEB_RESEARCH_UNAVAILABLE": "SOURCE_TRANSIENT",
    "WEB_NO_RESULTS": "EMPTY_RESULT",
    "KNOWLEDGE_UNAVAILABLE": "SOURCE_TRANSIENT",
    "ENTITY_RESOLUTION_UNAVAILABLE": "UNSUPPORTED_CAPABILITY",
    "SCOPE_MISMATCH": "SCOPE_MISMATCH",
    "POLICY_BLOCKED": "POLICY_BLOCKED",
    "MODEL_UNAVAILABLE": "MODEL_UNAVAILABLE",
    "INTERRUPTED": "INTERRUPTED",
    "UNCERTAIN": "UNCERTAIN",
}


def classify(code: str) -> ToolOutcomeCode:
    if not code:
        return "SUCCESS"
    return _CODE_MAP.get(code, "INTERNAL_FAILURE")


def normalize_outcome(tool_name: str, outcome: ToolOutcome | None,
                      error: BaseException | None = None) -> ToolOutcome:
    """Convert any tool return / raised exception into a durable ToolOutcome."""
    if error is not None:
        return ToolOutcome(recovery_code="INTERNAL_FAILURE",
                           detail=f"{tool_name}: {type(error).__name__}: {error}")
    if outcome is None:
        return ToolOutcome(recovery_code="INTERNAL_FAILURE",
                           detail=f"{tool_name} returned no outcome")
    return outcome


def attempt_for(*, attempt_id: str, request_id: str, need_id: str, capability: str,
                outcome: ToolOutcome, artifacts: tuple[RuntimeArtifact, ...] = (),
                binding_ids: tuple[str, ...] = (),
                external_effect_possible: bool = False) -> ToolAttempt:
    code = classify(outcome.recovery_code)
    if not outcome.recovery_code and artifacts and all(
            item.status == "EMPTY" for item in artifacts):
        # An empty result is a real outcome, never "success with no evidence".
        code = "EMPTY_RESULT"
    if artifacts:
        status = "SUCCEEDED" if code == "SUCCESS" else "FAILED"
    else:
        status = "FAILED"
    if code == "INTERRUPTED":
        status = "INTERRUPTED"
    elif code == "UNCERTAIN":
        status = "UNCERTAIN"
    retryable = code in ("SOURCE_TRANSIENT", "MODEL_UNAVAILABLE", "COVERAGE_UNAVAILABLE")
    from app.models.artifact_runtime import utcnow
    return ToolAttempt(
        attempt_id=attempt_id, request_id=request_id, need_id=need_id,
        capability=capability, status=status, outcome_code=code,
        detail=(outcome.detail or "")[:500],
        artifact_ids=tuple(item.artifact_id for item in artifacts),
        binding_ids=binding_ids, retryable=retryable,
        external_effect_possible=external_effect_possible, finished_at=utcnow())


def gap_for_attempt(need_id: str, attempt: ToolAttempt) -> str:
    """Recovery feedback: a failed action must always produce a coverage gap."""
    return (f"action {attempt.capability!r} for need {need_id!r} did not produce usable "
            f"evidence ({attempt.outcome_code})"
            + (f": {attempt.detail}" if attempt.detail else ""))
