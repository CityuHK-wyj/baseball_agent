# Next Steps After Architecture Baseline v1

This is the current bounded priority list, not a declaration that historical roadmap
milestones are complete. See [Implementation Status](../engineering/IMPLEMENTATION_STATUS.md)
for the implemented surface and [decision index](../engineering/DESIGN_DECISIONS.md)
for original design records. This cycle does not begin UAT or framework integration.

## 1. Review the publication Pull Request

- Review the proposed Architecture Baseline v1 source snapshot against the public
  repository's older v0.1 `main`; the histories are unrelated.
- Review security exclusions, documentation links, installation instructions and
  known limitations before merge. Do not publish the local development history.
- Do not move the protected `m2.12r4` tag or claim UAT completion.

## 2. Scoped semantic utility work (still open)

| ID | Required work and acceptance direction |
|---|---|
| U-1 | English per-player paraphrase source-review rejection: diagnose source-clause ownership/consumption, improve interpretation/grounding, evaluate fresh paraphrases rather than growing a wording whitelist. |
| U-2 | Split per-player requests fail closed before execution: trace source spans, per-Requirement scope and output units into requested contracts. Preserve fail-closed safety while measuring unnecessary rejection. |
| F-3/F-5 | Incomplete multi-subject analytical capabilities: extend typed output/granularity, population, qualification, ordering, comparison and enumeration proofs only where supported end to end. Existing subject-cell admission is not universal completion. |
| F-4 | Forecast execution limitations: distinguish prediction research from a calibrated forecast computation contract; no invented future values or unvalidated models. |

Preserve `wrong semantic COMPLETE = 0` as an evaluated hard safety metric while
measuring utility, clarification, correct execution and numerical accuracy. Finite
passing tests are not proof of universal correctness. Require fresh adversarial,
multilingual, clause-reordering and metamorphic checks when language behavior changes.
No production implementation may know frozen evaluation wording.

## 3. Complete bounded architecture targets separately

- Broaden governed operations only with requested/executed/output correspondence and
  exact-cell ancestry. Filtered/grouped/window roots require a typed scope extension,
  not relabelled free Python. Preserve recursive admission and proof budgets.
- Implement true Goal-wide research quota ownership, reservation/settlement and soft/hard
  deadlines before claiming M3. Cache must preserve retrieval/revalidation/as-of and
  representation versions. Current default budget metadata is not enforcement.
- Evolve adapter exposure without changing Requirement/Planner/Judge responsibility.
  Preserve compatibility of serialized kinds/statuses; use the
  [deferred rename map](../architecture/NAMING_REFACTOR_MAP.md#deferred-renames), not
  an unlimited naming-refactor loop.
- Add durable artifact/event storage, explicit retraction/invalidation semantics and
  safe interruption/idempotency before multi-worker runs, cancellation or live token
  streaming. UI retention is not durable evidence governance.
- Before public multi-user service: authentication/authorization, rate limits, deployment
  controls, shared session/event coordination and retention cleanup. This is future
  engineering, not a security-audit certification for the present prototype.

Do not merge types to reduce naming variation without proving semantic identity from
owner, construction, consumers, fields, lifetime, authority and serialization. The
[Domain Concept Registry](../architecture/DOMAIN_CONCEPT_REGISTRY.md) is the starting
point for future coding agents.
