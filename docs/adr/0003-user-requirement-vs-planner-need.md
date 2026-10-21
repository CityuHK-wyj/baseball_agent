# ADR 0003 — UserRequirement vs PlannerNeed

Status: accepted

## Decision
`UserRequirement` describes what the user asked for and owns completion semantics.
`PlannerNeed` is a work item the Planner creates to satisfy it. The Planner may add,
repair or cancel Needs, but may not alter the user's requirements or promote its own
optional investigation to CORE.

## Rationale
A failed news search must not invalidate a statistically supported answer.

## Consequences
- Criticality of a Need is derived deterministically: a Need is CORE only if it serves a
  CORE UserRequirement or is a transitive prerequisite of one.
- Goal status is computed from RequirementAssessment, never from Need success alone.
