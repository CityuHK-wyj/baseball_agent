# ADR 0004 — Planner Contract: Objective / Capability / Dependency

Status: accepted

## Decision
Planner output is a small Need graph:
`objective`, `capability`, `depends_on`, `input_requirements`, `desired_output`,
`required_evidence`, open `constraints`. It contains no physical table, column, SQL,
Safe IR, Artifact id or completion verdict.

## Rationale
Previous systems let the Planner emit database programs, which then failed validation and
polluted replanning. Late binding must extend from Semantics through Execution.

## Consequences
- Replanner repairs the existing graph (reuse / retry / replace edge / add genuinely new
  Need) instead of regenerating equivalent Needs.
- Physical convergence is owned by the capability, not the Planner.
