# ADR-draft 01 — Tool and Acquisition Model, and Shared Knowledge

Status: **accepted architectural decision; staged implementation in progress.**
Accepted by implementation directive; retained at the original draft path for continuity.
Extends ADR 0026 (single research capability) and ADR 0030 (official structured sources).

## Context

The registry exposes seven Planner-visible capabilities. Five are acquisition-adjacent
(`local_analytics`, `web_research`, `dynamic_fact`, `shared_knowledge`, `entity_resolution`).
ADR 0026/0030 already place structured record retrieval *inside* `web_research`, but
`dynamic_fact` remains a separate top-level capability whose untyped relation and caller
`as_of` cause F-02. Shared Knowledge is exposed as a peer acquisition tool rather than as a
cross-cutting semantic substrate.

## Decision

1. The domain Planner primarily chooses between **two acquisition/execution families**, `LOCAL_DATA_ANALYTICS` and `WEB_EXTERNAL_RESEARCH`:
   - `local_analytics` — Local DB / Analytics (retrieval + aggregation over raw source data,
     Safe IR, measure verification, execution receipt, `EnumerationContract`).
   - `web_research` — Web / External Acquisition (prose + structured external APIs as internal
     adapters).
2. Entity resolution, Shared Knowledge, projection/conversion and safe Python computation
   are supporting execution infrastructure, not independent domain sources. Existing typed
   CapabilitySpecs may remain as compatibility adapters during migration. Planner determines
   required work/evidence; execution infrastructure selects identity resolution, projections,
   provider adapters and computation. Do not add top-level Planner tools.
3. Structured external records are **acquisition adapters + evidence representations**, not
   peer tools. `dynamic_fact` is demoted to a structured adapter (currentTeam, roster, status)
   and must emit a typed structured record (relation, subject, provider record identity,
   as_of/event time, source role).
4. Distinctions that matter semantically (relation, temporal basis, source role, operation,
   representation) live at **evidence-representation level**. Provider mechanics live at
   **acquisition-adapter level**. The Planner sees only capability families and output types.
5. **Shared Knowledge** is a cross-cutting semantic substrate + knowledge registry,
   not a third acquisition family. Interpreter, RequirementBuilder, Planner, expectation
   derivation, Evidence Processing, Judge and Composer may read stable semantics:
   - stable content (`SEMANTIC`): read-only interpretation context; no factual authority;
   - mutable content (`MUTABLE_FACT`): evidence; passes Artifact → Evidence → Judge and is
     surfaced as QualifiedEvidence when unverified. Presence in the registry grants no
     current factual authority.
6. Structured evidence includes provider/record/subject identity, typed relation and field
   semantics, retrieval time, actual supported event/as-of time, source role and proposition
   linkage. Same player is not same proposition: currentTeam establishes neither injury
   status nor arbitrary historical team membership. Caller as_of never rewrites provider time.

## Invariants

- The Planner surface stays small; no new peer tool per provider or API.
- `web_research` never aggregates or derives measures (ADR 0026/0030 unchanged).
- Raw-data aggregation stays in `local_analytics`; `python_compute` remains the bounded
  cross-artifact exception.
- Shared semantic context assists interpretation; mutable Shared Knowledge never silently
  satisfies a current factual Requirement.
- No capability may mint a factual Claim; only the Judge approves.

## Consequences

- F-02's special path disappears when structured records are admitted proposition-locally.
- Routing distinguishes *evidence needs*, not adapters.
- Existing WSA-08 current-team controls become adapter regression tests.

## Non-goals

- No rewrite of Safe IR, the Binder, the SQL guard or the Web verification gate.
- No change to ADR 0003/0004/0016/0021/0022/0025.
