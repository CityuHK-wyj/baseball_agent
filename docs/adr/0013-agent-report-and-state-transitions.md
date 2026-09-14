# AgentReport envelope, explicit state transitions and dependency-ordered review

Status: accepted

## Context

P005, D043/D044 and posts 04/09 require a management envelope around domain results, a
review order that is not arrival order, and state domains that never mutate silently.
Only the state value plus a version existed; there was no report contract, no transition
record and no review step.

## Decision

- `app/models/report.py`: `AgentReport` is a reference envelope — identity, agent,
  `assignment_refs`, `status`, `summary`, `result_refs`, `state_refs`,
  `impact_proposals`, `requests`, `handoff`, `trace`. Domain results stay separate and are
  referenced. `DOMAIN_OWNERSHIP` maps each agent to the one state domain it owns, so
  `cross_domain_proposals` are exactly the transitions requiring Orchestrator review.
- `app/models/transition.py`: `StateTransition(domain, subject_ref, from_status,
  to_status, reason, trigger, version, created_at)`. A transition must change status.
- `app/agent/review.py`:
  - `ReportReviewer.review` returns DEFERRED for an unresolved prerequisite, REJECTED
    for FAILED/BLOCKED, READY_FOR_REVIEW when cross-domain transitions are proposed, else
    ACCEPTED.
  - `review_all` iterates to a fixed point: accepted/ready reports publish their
    `result_refs` so dependents can proceed, regardless of arrival order. A prerequisite
    cycle stays DEFERRED.
  - `StateTransitionLog.apply` enforces continuity (`from_status` matches the current
    status) and monotonic versions, and keeps per-subject history. Only the Orchestrator
    applies reviewed transitions (`apply_report_proposals`).

## Alternatives

- Apply reports in arrival/first-come order: rejected; a dependent report would be
  reviewed before its input exists.
- Let sub-agents write global state directly: rejected by D044.
- Put the domain result inside the report: rejected by P005; the report is a cover sheet.

## Consequences

- Cross-domain change is now explicit and auditable; silent mutation is rejected.
- The Orchestrator does not yet emit or consume `AgentReport`s in its loop; that wiring
  is a follow-up and must keep `StateTransitionLog` as the only applier.
- `review_all` is deterministic and side-effect free; it does not itself apply anything.
