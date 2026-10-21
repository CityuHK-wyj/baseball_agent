# ADR 0033 — Frontend Interaction & Presentation Boundary

Status: Accepted (frontend foundation). Starting commit: 66f1f5712b87c38e69be9a143af597d44f10d473.

## Context

The repository has one Python `Engine.ask` and SQLite-backed `ConversationManager`; no HTTP API or UI. `Engine.ask` is synchronous and commits a completed turn, including a pending `Clarification`. The Judge, not the browser, decides coverage and status. `AnswerPackage` is an internal composer input, not a network contract. The current Judge reports mixed success as LIMITED and a clarification as WAITING_FOR_USER; it does not emit PARTIAL or TEMPORARILY_UNRESOLVED GoalStatus. Database artifacts contain SQL/IR and may include rejected rows; exporting them verbatim would bypass the Judge.

## Decision

Frontend collects user intent; backend owns semantics. **Frontend state is not factual authority.** React renders only typed, server-projected presentation envelopes. Only Judge-supported artifacts/claims are promoted to user-visible results. Internal artifacts, Requirements, evidence and planning metadata are never turned into factual conclusions by the browser. No model-generated executable UI. A debug view may show *bounded, structured* operational data, not chain-of-thought, raw SQL, secrets or unrestricted diagnostic dumps.

FastAPI adds a thin adapter around the existing Engine, with REST commands and replayable SSE events. A message command starts one run and returns IDs; clarification submission validates the pending question and opaque option IDs on the server and resumes through `Engine.ask`, which remains the sole interpreter of the selected *backend-provided* option text. Runs are serialized per Engine instance to avoid cross-session mutable LLM/Judge state. Browser never submits Requirements or entity IDs. Events are versioned discriminated envelopes, sequential run-scoped IDs, and replayed after `Last-Event-ID`/`after`. Unknown event types are displayed as unsupported activity; invalid known events fail closed with a diagnostic. An ended stream without terminal event is an error. In-process replay is not durable across API process restarts; persistent conversation text and pending clarification are loaded from the existing SQLite store.

The adapter does not claim to stream tokens from `Engine.ask`: it emits real run start/status immediately and emits the *completed, Judge/consistency-approved* answer as one idempotent delta when `ask` returns. Actual token-by-token streaming requires a future approved Engine callback with safe provisional-text rules. Partial approved results remain visible across turns; an unresolved requirement does not erase an earlier supported result. WAITING_FOR_USER is displayed distinctly from LIMITED; PARTIAL is reserved in the presentation schema for a future authoritative backend state, never synthesized by UI heuristics.

Backend Pydantic API models own the wire schema and OpenAPI. Frontend types are generated from OpenAPI, with Zod runtime validation at the ingress boundary. Known incompatible changes fail tests; unknown future events degrade gracefully. TanStack Query caches snapshots, not truth; local reducer owns transient stream state. Small tables use TanStack Table; future paged tables and specialized charts can extend the presentation union without changing the semantic core. Recharts accepts only typed bar/line/scatter data, never executable specs.

Transport/validation errors are distinct from provider/unsupported/semantic/run errors. Credentials, raw SQL and LLM secrets stay server-side. Vite dev proxy gives same-origin `/api`; production can use static assets with same-origin reverse proxy or separately hosted static UI with narrowly allowed origin. No WebSockets/Redux/GraphQL/Node BFF.

## Consequences

Real Engine operations can be slow and source/provider-dependent. Cancel/retry are deferred until interruption/idempotent run semantics exist. No fallback to a guessed identity or invented analytics. Memory-backed run replay is a deliberate MVP limitation; for multi-worker deployment use a durable event log before scaling.

## Amendment (2026-10-06) — Requirement-local partial approval

Approval is **Requirement-local**; completion is **Goal-level**. The backend now admits an
analytical Artifact against the exact current Requirements it independently conforms to
(graph-derived partitions plus the persisted execution receipt), so an independent
unresolved Requirement can no longer erase another Requirement's approved Artifact. The
partition decision is semantic/authoritative: a finite lexical task-verb list is **not**
used to decide independence (see `docs/audit/REQUIREMENT_LOCAL_PARTIAL_APPROVAL.md` §4).

No new `GoalStatus` was added. `COMPLETE` means all mandatory Requirements satisfied;
`LIMITED` means at least one satisfied and at least one unresolved/partial;
`WAITING_FOR_USER` means progress needs user input and may already carry approved partial
facts; `FAILED` means no useful supported result. The presentation-layer `PARTIAL` value
remains reserved for a future authoritative backend state and is still never synthesized
by browser heuristics. The documented semantics and same-Goal evidence are in
`docs/audit/REQUIREMENT_LOCAL_PARTIAL_APPROVAL.md`.
