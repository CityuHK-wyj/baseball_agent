"""Functional broad-search gap assessment (M2-6).

When a Goal remains unresolved, the useful question is *where* the information pipeline
failed, because that decides the next development step. The six categories are:

- ``QUERY_FAILURE``            — no provider returned a hit for any facet;
- ``RANKING_FAILURE``          — hits existed but none mentioned the subject/relationship;
- ``FETCH_FAILURE``            — relevant candidates existed but no body could be retrieved;
- ``SPAN_FAILURE``             — a relevant body was retrieved but no passage carrying the
                                 requested relation was selected;
- ``VERIFICATION_FAILURE``     — a relevant passage was selected but the verifier could not
                                 establish the proposition;
- ``SOURCE_COVERAGE_FAILURE``  — every attempted source failed (auth/blocked/timeout) so no
                                 discovery was possible at all.

This is diagnostic only. It does not change retrieval and does not fabricate a category when
the evidence is ambiguous: it returns ``UNKNOWN`` and the caller must record why.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

FAILURE_CATEGORIES = (
    "QUERY_FAILURE",
    "RANKING_FAILURE",
    "FETCH_FAILURE",
    "SPAN_FAILURE",
    "VERIFICATION_FAILURE",
    "SOURCE_COVERAGE_FAILURE",
    "NONE",
    "UNKNOWN",
)


@dataclass(frozen=True)
class RetrievalSignals:
    """The minimum observable signals needed to locate the first material failure."""

    provider_calls: int = 0
    provider_errors: int = 0
    all_providers_failed: bool = False
    hits: int = 0
    relevant_hits: int = 0
    fetched_ok: int = 0
    selected_spans: int = 0
    supported_findings: int = 0
    mandatory_unsupported: bool = True
    failure_reason: str = ""


def classify_failure(signals: RetrievalSignals) -> str:
    """Return the first material failure category, or ``NONE`` when nothing is unresolved."""
    if not signals.mandatory_unsupported:
        return "NONE"
    if signals.all_providers_failed or (
        signals.provider_calls > 0 and signals.provider_errors >= signals.provider_calls and signals.hits == 0
    ):
        return "SOURCE_COVERAGE_FAILURE"
    if signals.hits <= 0:
        return "QUERY_FAILURE"
    if signals.relevant_hits <= 0:
        return "RANKING_FAILURE"
    if signals.fetched_ok <= 0:
        return "FETCH_FAILURE"
    if signals.selected_spans <= 0:
        return "SPAN_FAILURE"
    if signals.supported_findings <= 0:
        return "VERIFICATION_FAILURE"
    # Support exists somewhere but not for the mandatory relation: the selected passages were
    # the wrong ones for this obligation.
    return "SPAN_FAILURE"


def classify_run(run: dict[str, Any]) -> str:
    """Classify a benchmark run from its persisted fields where possible.

    The benchmark report does not persist discovery counts, so this classifies only when the
    available evidence is unambiguous and returns ``UNKNOWN`` otherwise. The dogfooding
    harness records :class:`RetrievalSignals` directly for a precise classification.
    """
    mandatory_total = int(run.get("mandatory_total") or 0)
    mandatory_satisfied = int(run.get("mandatory_satisfied") or 0)
    if mandatory_total and mandatory_satisfied >= mandatory_total:
        return "NONE"
    findings = run.get("evidence_findings") or []
    supported = [f for f in findings if f.get("status") == "SUPPORTED"]
    fetched = int(run.get("fetched_documents") or 0)
    provider_calls = int(run.get("provider_calls") or 0)
    provider_errors = int(run.get("provider_errors") or 0)
    if provider_calls == 0:
        return "UNKNOWN"
    if fetched == 0 and provider_errors >= provider_calls:
        return "SOURCE_COVERAGE_FAILURE"
    if fetched == 0:
        return "SPAN_FAILURE"
    if findings and not supported:
        return "VERIFICATION_FAILURE"
    if supported:
        return "SPAN_FAILURE"
    return "UNKNOWN"


def breakdown(runs_by_task: dict[str, list[dict[str, Any]]], task_ids: Iterable[str] | None = None) -> dict[str, Any]:
    """Aggregate failure categories across tasks (first material failure per task)."""
    counts: dict[str, int] = {category: 0 for category in FAILURE_CATEGORIES}
    per_task: dict[str, str] = {}
    for task_id in task_ids or runs_by_task:
        runs = runs_by_task.get(task_id) or []
        category = "UNKNOWN"
        for run in runs:
            category = classify_run(run)
            if category not in {"NONE", "UNKNOWN"}:
                break
        per_task[task_id] = category
        counts[category] = counts.get(category, 0) + 1
    return {"per_task": per_task, "counts": {key: value for key, value in counts.items() if value or key == "NONE"}}
