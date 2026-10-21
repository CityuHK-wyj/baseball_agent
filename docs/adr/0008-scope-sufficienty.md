# ADR 0008 — Scope-Aware Sufficiency and Partial Answers

Status: accepted

## Decision
Every Artifact carries `requested_scope` and `actual_scope`. Sufficiency compares them on
entity, time, season, game population, measure, source coverage and provenance. A broader
window does not satisfy a narrower request.

Statuses: Requirement `SATISFIED / PARTIAL / UNSATISFIED / BLOCKED`; Goal
`COMPLETE / LIMITED / FAILED / WAITING_FOR_USER`.

## Consequences
- Incomplete evidence reduces answer scope; it never erases supported conclusions.
- Indispensable prerequisites (e.g. a transaction date) still block the claim that
  depends on them.
