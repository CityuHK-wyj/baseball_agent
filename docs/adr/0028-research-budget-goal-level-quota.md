# ADR 0028 — Research Budget: Goal-Level Quota and Deadline Model

Status: accepted (Web Research v2 architecture, 2026-09-27)

Architecture reference: `docs/architecture/WEB_RESEARCH_V2.md` §7.
Implementation milestone: M3 (budget and deadline enforcement). M1 declares the budget type
and records `research_metrics`; it does not enforce the full quota or cache behavior.

## Context

Phase 5B bounds research only with `max_fetches=4` per request. The outer `max_rounds`
bounds Planner rounds, not research work inside a round. Because each `web_research` call
would receive a fresh quota, a multi-round Goal could repeat the full provider/fetch/LLM
spend and multiply latency and cost. There is no distinction between a single slow provider
call and a Goal-level deadline, and no defined behavior for work that is still in flight when
a deadline fires.

A naive budget owned by the tool would reset on every Planner replan (terminal-Need repair,
second research Need, repeated round), which defeats the purpose.

## Decision

### Ownership

A single `GoalResearchBudget` is held by the **RuntimeContext / Goal State** (the per-Goal
`ExecutionContext` created by the Engine), keyed by `goal_id`, and created exactly once when
the Goal is created. `WebResearchTool` receives a handle and may only **consume** from it. It
cannot create, re-create, raise or reset the budget. A Planner replan reuses the same
instance, so a multi-round Goal can never recover spent quota.

### Two scopes

- **Goal-level** `GoalResearchBudget`: `goal_provider_calls`, `goal_documents_fetched`,
  `goal_llm_query_calls`, `goal_llm_verify_calls`, `goal_search_rounds`,
  `goal_soft_deadline_ms`, `goal_hard_deadline_ms`.
- **Per-call** `ResearchBudget`, always clamped to the remainder:
  `effective = min(tool_default, goal_remaining)`.

### Three clocks

- **Provider timeout** (`provider_timeout_ms` / `fetch_timeout_ms`): the maximum for one
  network call. Exceeded → typed `TIMEOUT`, one bounded retry if budget allows, then that
  candidate fails while other work continues.
- **Soft deadline**: the pipeline stops launching new work (provider calls, fetches, LLM
  verifications) and drains in-flight work inside a grace window.
- **Hard deadline**: in-flight work is cancelled and the tool finalizes with whatever is
  already verified. The hard deadline is strictly below the latency objective it protects.

### In-flight semantics

- **Actual timeout**: each call keeps its own per-call timeout; shared deadlines are
  evaluated independently. A call whose per-call timeout would extend past the hard deadline
  is not launched after the soft deadline and is cancelled at the hard deadline.
- **Cancellation**: outstanding futures are cancelled; already-completed futures are left
  untouched. Results not completed by the finalization point are not admitted, even if they
  complete momentarily later, so the artifact is a reproducible snapshot.
- **Result admission**: only results that completed before finalization and pass the
  admissibility/offset contract are admitted. A late or cancelled result is dropped, never
  merged retroactively. Fetched documents remain as provenance even when no span is admitted;
  a partial HTTP body is never treated as a document.
- **Budget settlement**: runs in a `finally` for every future. Reserved-but-unstarted calls
  are released; started calls are counted as used whether they succeeded, failed, timed out
  or were cancelled. Counters never fall below the number of calls that actually started.
  Reservations are made before launch (`try_reserve`) and settled after.

### Partial results

On deadline or counter exhaustion the tool returns a `PARTIAL` `WEB_EVIDENCE` artifact with
the typed outcome `BUDGET_EXCEEDED` (`retryable=False` when the Goal budget is exhausted,
`retryable=True` for a per-call soft stop while Goal budget remains) and a machine-readable
`budget_report`. Verified work is kept; unverified candidates are omitted; the Judge decides
coverage from the same evidence shape. A partial artifact can yield `LIMITED`, never a
fabricated `COMPLETE`.

## Invariants

- The Goal-level budget is monotonic across all Planner rounds and cannot reset on replan.
- Budget counters never decrease below the number of calls that actually started.
- The budget never fails the Goal by itself; exhaustion is a typed, honest degradation.
- The outer Runtime bounds (`max_rounds≤5`, `max_needs=8`) remain the authority on Planner
  rounds and are unchanged.

## Consequences

- Total research latency and cost per Goal are bounded and measurable.
- The p50 target sits below the soft deadline and the p95 target above the hard deadline, so
  normal runs are not truncated and the tail degrades to a verified partial result.
- Real provider usage is recorded and available for the benchmark (ADR 0024 semantics kept).

## Non-goals

- No change to the Judge, Evidence Verifier or Composer.
- No retry storm or unbounded parallelism.
- No claim of achievable latency before the benchmark runs.
