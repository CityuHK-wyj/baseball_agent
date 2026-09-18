# ADR 0028 — DeepSeek Flash runtime default and bounded LLM orchestration

Status: Accepted
Date: 2026-06 (v0.6 DeepSeek Flash latency round)
Context branch: `pi/v0.6-deepseek-flash-latency`

## Context

The v0.5 runtime-performance audit (`docs/reviews/v05-runtime-performance/`) measured a
single uncapped planner completion of **710.8 s / 15,856 completion tokens** and calls of
92 s / 369 s that the configured 60 s provider `timeout` did not bound. A simple user
question invoked the configured model multiple times (semantic + planner initial + 0–6
replans + composer), each emitting thousands of hidden reasoning tokens. User-visible
latency was dominated by model inference, not by tools, the database, the Judge, or
persistence.

Two connected problems had to be solved at the same time:

1. the runtime had no single authoritative product-path model, so a slow reasoning model
   was the default for every role;
2. there was no true end-to-end model-call deadline and no role-specific output bound, so
   one call could occupy the turn indefinitely.

## Decision

1. **One authoritative runtime model.** `app/config.py` defines
   `DEFAULT_RUNTIME_MODEL = "deepseek-flash"` and a single resolution chain
   `RUNTIME_MODEL → DEEPSEEK_MODEL → deepseek-flash`. Every product-path cognition role
   (`semantic`, `planner`, `response`, `judge`) resolves through it. Role-specific
   environment overrides remain (`SEMANTIC_MODEL`, `PLANNER_MODEL`, `RESPONSE_MODEL`,
   `JUDGE_MODEL`). The legacy dual-semantic roles share the same runtime model instead of
   a separate hard-coded default. Model names are never scattered through modules.

2. **Role-specific output bounds.** Reasoning tokens count against `max_tokens`, so each
   structured role has a conservative bound derived from its schema:
   semantic `1024`, planner `2048`, response `1024`, judge `512`. The default reasoning
   effort is `none` so a structured role actually emits its typed output within the bound;
   a deployment may raise `LLM_REASONING_EFFORT` at a latency cost.

3. **True end-to-end deadline.** The OpenAI-compatible provider streams completions and
   enforces a wall-clock deadline covering request, provider wait, stream reads and
   completion. The SDK per-operation timeout is additionally bounded by the deadline so a
   stall before the first token cannot outlive it. On overflow the provider raises
   `ProviderTimeout`, and the existing safe fallbacks engage; a timeout is never silent
   success. A `finish_reason=length` with no visible content is a typed bounded-output
   failure, not an empty success.

4. **Bounded replanning.** A turn may not chain an unbounded number of full semantic
   replans (`_MAX_REPLANS = 2`), and a repeated identical coverage-gap signature is a
   deterministic continuation, not another model call. Recovery opportunities — including
   alternate-route replanning — are preserved.

5. **Deterministic clarification resume.** A reply that selects a machine-generated
   clarification option is bound from the persisted semantic brief without re-running the
   semantic model. Free-text replies still fall back to semantic interpretation.

6. **Native latency telemetry.** The `EventJournal` records a `LATENCY` event per runtime
   phase (semantic, planner initial/replan/context/scheduler, binding, tool, IR compile,
   DB execute, scope verification, judge, verification, claim grounding, state projection,
   response composition, persistence). `RuntimeTrace.latency` is a per-turn phase→ms map
   and `--trace` prints a compact summary. No prompts, payloads or hidden reasoning are
   recorded.

## Authority boundaries (unchanged)

DeepSeek Flash may propose only. Immutable User Obligations, requested/declared/verified
scope, ToolOutcome, artifact verification, the independent (deterministic) Judge, state
projection, Safe Analytical IR, SchemaCatalog, explicit InputBinding, claim grounding,
Candidate ≠ ACTIVE knowledge, persistence identity, the SQL guard, read-only roles and the
SSRF guard are all unchanged and remain authoritative.

## Consequences

* A live benchmark turn now costs 3–28 s instead of 100–700 s, with 1–5 model calls.
* Role output is bounded, so a planner can no longer emit an essay of hidden reasoning.
* A slow or stalled provider cannot occupy a turn past the deadline; behavior degrades to
  the deterministic fallback rather than hanging.
* Setting `LLM_REASONING_EFFORT=low|medium|high` restores reasoning at a latency cost;
  this is a deployment decision, not a code change.

## Alternatives considered

* **Disable streaming and rely on a thread-based watchdog.** Rejected: thread leakage and
  weaker cancellation semantics.
* **Set a hard planner `max_tokens` without disabling reasoning.** Rejected: reasoning
  consumes the budget and produces empty content, causing fallback storms.
* **Keep the slow reasoning model and only add a deadline.** Rejected: every turn would
  hit the deadline and fall back to the deterministic planner, losing analytical planning.
* **Hard-skip replanning for unsupported capabilities.** Rejected: it removes a legitimate
  alternate-route recovery. Replaced by a bounded replan budget and gap dedupe.
