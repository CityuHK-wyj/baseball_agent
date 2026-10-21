# ADR 0006 — SQL-Specific Semantic Convergence

Status: accepted

## Decision
Open analytical intent converges only at the database boundary:
`AnalyticalIntent -> SchemaCatalog slice -> Safe Analytical IR -> validation ->
deterministic SQL compiler -> read-only guard -> read-only execution`.
The LLM may help draft an IR candidate; every identifier and operator is validated
against the catalog and arbitrary SQL is unrepresentable in the IR.

## Rationale
Web/Knowledge do not need SQL-like narrowing; the database does, because it executes
precise computation over structured data.

## Consequences
- `MetricRegistry` is a documentation layer for canonical metrics, not a gate on ad-hoc
  analysis. Any analysis expressible from catalog fields is allowed.
- Unknown field / unsupported operation fails closed with a structured code.
- Competition population compiles to a `game_type` predicate, never a calendar window.
