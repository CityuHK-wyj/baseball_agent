"""AgentReport: the management envelope around a domain result.

A report is a cover sheet: identity, assignment, status, summary, references and
proposed cross-domain effects. The professional attachment (RoutingDecision,
JudgeAssessment, ExecutionPlan, ...) stays separate and is referenced by ``result_refs``
(P005).
"""

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.models.artifacts import ArtifactContract, utcnow
from app.models.contracts import Name
from app.models.transition import StateDomain, StateTransition

AgentKind = Literal[
    "SEMANTIC", "REQUIREMENT_DECOMPOSER", "PLANNER", "ROUTER", "EXECUTOR",
    "FEATURE_ENGINE", "EVIDENCE", "JUDGE", "RESPONSE", "ORCHESTRATOR",
]
ReportStatus = Literal["SUCCEEDED", "EMPTY", "FAILED", "BLOCKED", "PARTIAL"]
ReviewStatus = Literal["RECEIVED", "READY_FOR_REVIEW", "ACCEPTED", "REJECTED", "DEFERRED"]

# Which state domain each agent owns locally. Anything else needs Orchestrator review.
DOMAIN_OWNERSHIP: dict[str, StateDomain] = {
    "SEMANTIC": "QUERY",
    "REQUIREMENT_DECOMPOSER": "REQUIREMENT",
    "PLANNER": "PLANNING",
    "ROUTER": "ROUTING",
    "EXECUTOR": "EXECUTION",
    "FEATURE_ENGINE": "ARTIFACT",
    "EVIDENCE": "ARTIFACT",
    "JUDGE": "ASSESSMENT",
    "RESPONSE": "INTERACTION",
    "ORCHESTRATOR": "OBJECTIVE",
}


class AgentReport(ArtifactContract):
    report_id: Name
    agent: AgentKind
    assignment_refs: tuple[Name, ...] = ()
    status: ReportStatus = "SUCCEEDED"
    summary: str = ""
    result_refs: tuple[Name, ...] = ()
    state_refs: tuple[Name, ...] = ()
    impact_proposals: tuple[StateTransition, ...] = ()
    requests: tuple[str, ...] = ()
    handoff: str = ""
    trace: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=utcnow)

    @property
    def owned_domain(self) -> StateDomain:
        return DOMAIN_OWNERSHIP[self.agent]

    @property
    def cross_domain_proposals(self) -> tuple[StateTransition, ...]:
        return tuple(item for item in self.impact_proposals if item.domain != self.owned_domain)
