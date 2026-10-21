# ADR 0001 — Separate Repository and Clean-Room Rebuild

Status: accepted (2026-09-21)

## Context
The legacy project accumulated overlapping responsibilities, compatibility layers,
complex Planner protocols and historical state contamination. Multiple audits concluded
that incremental patching no longer converges.

## Decision
Build a new, independent Git repository. Reuse only verified business facts: real
database schema, data coverage, safety boundaries and testing ideas. Do not copy the
legacy Runtime, Planner, Semantic parser, Orchestrator or Tool adapters. Do not modify or
push the legacy repository.

## Consequences
- One authoritative data flow exists in this repository.
- Legacy behavior is not a compatibility constraint.
- Business correctness (field meaning, coverage, population semantics) is re-verified
  against the live sources, not copied from reports.
