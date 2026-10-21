# ADR 0026 — Single-Capability, Multi-Source Web Research

Status: accepted (Web Research v2 architecture, 2026-09-27)

Supersedes: none. Amends: none (ADR 0021–0025 are preserved unchanged).
Architecture reference: `docs/architecture/WEB_RESEARCH_V2.md` §3, §3.5.
Implementation milestones: M1 (contracts + keyless Wikipedia path), M2, M3, M4.

## Context

The Phase 5B baseline exposes exactly one research capability, `web_research`, backed by a
small set of backends (publisher feeds, an optional unconfigured JSON endpoint, Wikipedia —
live again as of 2026-09-27 — and discovery-only Google News). The audit and the kickoff
identify the capability gap as *discovery and retrieval*, not semantic interpretation: no
real broad web search, shallow query planning, subject-only ranking, sequential retrieval,
no structured-source interoperability, and no research budget.

The obvious but wrong response is to expose every provider and structured API as a separate
Planner tool. That would inflate the Planner surface, fragment the Need graph, and invite
duplicated computation. It would also risk re-implementing statistics inside research.

## Decision

`web_research` remains the **only Planner-visible research capability** (`accepts:
SEARCH_QUERY`, `produces: WEB_EVIDENCE`). All provider selection, routing, ranking, fetching
and budgeting live **inside** `WebResearchTool.execute()` behind a stable interface:

1. **Source Registry** — a declarative `SourceDescriptor` per source (kind, discovery/full
   body/structured, historical/current, auth, `trust_weight`, latency class, rate limit),
   and a `SearchProvider` protocol that extends the existing `SearchBackend`. Existing
   backends are adapted, not replaced.
2. **Research Router** — a deterministic function
   `route(propositions, scope, freshness, available_sources) -> SourcePlan` keyed only on
   `AnswerProposition.kind`, time/population scope, and the existing `is_high_risk()`
   markers. It never inspects player or event names.
3. **Analytical vs research responsibility boundary** (fixed here):
   - `local_analytics` owns aggregation over raw PostgreSQL/Parquet source data (Safe IR,
     measure verification, `EnumerationContract`).
   - `python_compute` owns bounded temporary computation over **already-accepted Artifacts**;
     it does not read source tables and is not the default compute path.
   - `dynamic_fact` owns operational relation facts (team membership, roster/status) with
     `as_of`.
   - `web_research` only retrieves; it never aggregates, ranks, or computes derived measures.

No new Planner-visible capability or tool is introduced. Structured lookup remains internal
to `web_research`.

## Invariants

- The Planner sees exactly one research capability; the accepted Need graph and Binder are
  unchanged.
- Routing and source selection are deterministic and name-agnostic.
- `web_research` performs no aggregation and no derived computation; cross-record
  computation leaves Web Research entirely.
- The accepted single Runtime flow is preserved: nothing in ADR 0026 authorizes a parallel
  orchestration graph or a second agent loop.
- `verify_span`, the Judge, the Composer and diagnostics policy semantics are unchanged.

## Consequences

- Broad search, official sources, publisher feeds and structured APIs can be added behind one
  tool without changing the Planner contract.
- Provider gaps are hidden from the Planner but become a critical, testable internal routing
  component.
- The responsibility boundary prevents "just compute it in Python" from becoming the default;
  raw-data aggregation stays in SQL via `local_analytics`.
- The broad-search provider itself is configuration, not committed here (see ADR 0027/0030).

## Non-goals

- No new Planner tools, capabilities or orchestration layer.
- No change to ADR 0021–0025, the Binder, SQL guard, measure verification or enumeration.
- No named-player or named-event shortcuts anywhere in production code.
