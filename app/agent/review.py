"""Report review and cross-domain transition application.

The Orchestrator reviews reports in dependency order, not arrival order: a report whose
upstream prerequisite is unresolved is DEFERRED, and only accepted/ready reports feed
their results forward. Cross-domain effects are proposals that require review before
they are applied.
"""

from collections.abc import Iterable

from app.models.artifacts import ArtifactContract
from app.models.contracts import Name
from app.models.report import AgentReport, ReviewStatus
from app.models.transition import StateDomain, StateTransition


class ReviewDecision(ArtifactContract):
    report_ref: Name
    status: ReviewStatus
    reason: str = ""


class ReviewOutcome(ArtifactContract):
    decisions: tuple[ReviewDecision, ...] = ()
    accepted: tuple[Name, ...] = ()
    ready: tuple[Name, ...] = ()
    deferred: tuple[Name, ...] = ()
    rejected: tuple[Name, ...] = ()


class ReportReviewer:
    def __init__(self, rejected_statuses: tuple[str, ...] = ("FAILED", "BLOCKED")) -> None:
        self._rejected_statuses = rejected_statuses

    def review(self, report: AgentReport, resolved_refs: Iterable[str]) -> ReviewDecision:
        resolved = set(resolved_refs)
        unresolved = [ref for ref in report.assignment_refs if ref not in resolved]
        if unresolved:
            return ReviewDecision(report_ref=report.report_id, status="DEFERRED",
                                  reason=f"unresolved prerequisites: {sorted(unresolved)}")
        if report.status in self._rejected_statuses:
            return ReviewDecision(report_ref=report.report_id, status="REJECTED",
                                  reason=f"report status {report.status}")
        if report.cross_domain_proposals:
            return ReviewDecision(report_ref=report.report_id, status="READY_FOR_REVIEW",
                                  reason="report proposes cross-domain transitions")
        return ReviewDecision(report_ref=report.report_id, status="ACCEPTED")

    def review_all(self, reports: Iterable[AgentReport],
                   resolved_refs: Iterable[str] = ()) -> ReviewOutcome:
        ordered = tuple(reports)
        resolved = set(resolved_refs)
        decisions: dict[str, ReviewDecision] = {}
        pending = list(ordered)
        progress = True
        while progress and pending:
            progress = False
            remaining: list[AgentReport] = []
            for report in pending:
                decision = self.review(report, resolved)
                if decision.status == "DEFERRED":
                    remaining.append(report)
                    continue
                decisions[report.report_id] = decision
                resolved.update(report.result_refs)
                progress = True
            pending = remaining
        for report in pending:
            decisions[report.report_id] = ReviewDecision(
                report_ref=report.report_id, status="DEFERRED",
                reason="unresolved upstream prerequisites")
        result = tuple(decisions[report.report_id] for report in ordered)
        return ReviewOutcome(
            decisions=result,
            accepted=tuple(item.report_ref for item in result if item.status == "ACCEPTED"),
            ready=tuple(item.report_ref for item in result if item.status == "READY_FOR_REVIEW"),
            deferred=tuple(item.report_ref for item in result if item.status == "DEFERRED"),
            rejected=tuple(item.report_ref for item in result if item.status == "REJECTED"))


class StateTransitionLog:
    """Applies reviewed transitions and keeps a per-subject history. Orchestrator-only."""

    def __init__(self) -> None:
        self._history: dict[tuple[str, str], list[StateTransition]] = {}

    def apply(self, transition: StateTransition) -> StateTransition:
        key = (transition.domain, transition.subject_ref)
        history = self._history.setdefault(key, [])
        if history:
            previous = history[-1]
            if transition.from_status != previous.to_status:
                raise ValueError("transition does not continue from the current status")
            if transition.version != previous.version + 1:
                raise ValueError("transition version is out of order")
        elif transition.version != 0:
            raise ValueError("the first transition for a subject must be version 0")
        history.append(transition)
        return transition

    def apply_report_proposals(self, report: AgentReport) -> tuple[StateTransition, ...]:
        return tuple(self.apply(item) for item in report.impact_proposals)

    def current(self, domain: StateDomain, subject_ref: str) -> StateTransition | None:
        history = self._history.get((domain, subject_ref), [])
        return history[-1] if history else None

    def history(self, domain: StateDomain, subject_ref: str) -> tuple[StateTransition, ...]:
        return tuple(self._history.get((domain, subject_ref), []))
