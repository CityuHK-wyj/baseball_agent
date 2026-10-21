# Implementation Status — Architecture Baseline v1

Implementation anchor: `1d8cb3ad0ec786b48c82de830c6ad68b229c1511`, tree
`3cca1a0c28a2e377920e5516d23bb898f8e92178`. Baseline v1 adds documentation only.
The protected annotated `m2.12r4` tag is not moved or rewritten.

## Validation: historical versus this cycle

The latest **Naming Refactor validation**, recorded in the
[Naming Refactor Map](../architecture/NAMING_REFACTOR_MAP.md#validation-performed):

| Check | Recorded result |
|---|---|
| Backend offline | **4184 passed, 63 skipped** |
| Frontend unit | **10 passed** |
| Frontend production build | **Passed**; existing chunk-size warning |
| Focused live smoke | **5 passed** (4 Composer/Conformance + 1 Engine analytics) |
| OpenAPI compatibility | **5 paths / 35 schemas**, byte-identical to the pre-refactor baseline |

**Full live integration was not rerun after Naming Refactor.** These are historical
results from that cycle, not a claim that all suites were rerun during documentation
publication. Earlier ADR/audit totals describe their own historical trees.

Current documentation-cycle checks and limitations are recorded separately in
[Testing and Validation](TESTING_AND_VALIDATION.md). No new accuracy measurement,
UAT, full backend run, live integration or browser E2E is claimed.

## Capability maturity

| Area | Implemented | Limits / not implemented |
|---|---|---|
| Semantic interpretation | Model proposal, stable knowledge context, schema feedback, source-bound clauses, role/evidence/output metadata, finite grounding and residue | Open-language coverage is incomplete; finite fallback/source-review rejection and model variability remain. No universal paraphrase equivalence. |
| Requirement authority | Bound provenance, validated contextual/clarification ancestry and finite semantic transformations | Provenance is not interpretation proof. No arbitrary model-generated obligation becomes authoritative from IDs/prose alone. |
| Planner / runtime | Single Engine, validated Need graph, strict Binder, two domain families, seven visible specs, converter repair, bounded replanning | Supporting capability specs still exposed; family migration is not complete adapter hiding. No framework integration or parallel authority agents. |
| Local analytics | Canonical packs, Safe IR, deterministic compiler, SQL guard, read-only PostgreSQL/Parquet and observed receipts | Finite catalog/measures/operators/source windows, no arbitrary SQL, no general cross-source SQL union. Runtime source/data provisioning is external. ERA, wins/losses and saves are not inferred from unavailable pitch-level fields. |
| Derived operations | Finite `OperationContract`/`GovernedOperation`, trusted evaluation, exact-cell recursive root/parent admission | Current root proof is singleton scalar aggregate; grouped/filtered/window extensions and general forecasting are not authorized. AST-safe free Python is candidate computation, not factual proof. |
| Evidence / Judge | Span/representation replay, signatures, hard gates, typed source-role/assertion/temporal expectations, selective inspection, native membership, Requirement assessments | Retrieval breadth and bounded assessor/source review remain limiting. Current-team native records do not establish injury/transaction/history. Qualified evidence is not an approved fact. |
| Governed answer lifecycle | Deep immutable projection/package, untrusted draft, package/draft-bound review, two attempts, deterministic fallback, presentation telemetry | Arbitrary natural-language equivalence/translation is not guaranteed. Fallback can retain accepted source language and simplified formatting. |
| Partial results / subject units | Requirement-local independent partitions, exact per-subject scalar/grouped-row admission and Claim-linked row/field API projection | Shared obligations/dependencies still block unsafe subsets. Broad multi-subject analytics is incomplete; U-1/U-2 remain open. No formal artifact-retraction event. |
| Acquisition | Shared Safe Transport, pinned destinations/redirects, bounded decompression and telemetry, bounded content resolver | Active browser networking unavailable (`JS_RENDER_REQUIRED`); network/provider restrictions, public-source reachability, no paywall/auth bypass. No newly measured source coverage or latency guarantee. |
| Research quotas / cache | Local fetch/attempt/size bounds, budget contract types and reported counters; bounded post-Judge launch window | **No enforced shared Goal-wide M3 reservations/soft-hard cancellation lifecycle; no retrieval cache.** `app/web/cache.py` does not exist. Defaults reported as budget metadata are not spent-quota enforcement. |
| Shared Knowledge | ACTIVE stable semantic context, explicit candidate review and mutable/stable type classification | Not a mutable-fact authority cache; item authority strings are not independent proof. |
| Persistence / HTTP / UI | SQLite conversation/Goal/clarification, FastAPI REST/SSE, replay in memory, generated types/Zod, trusted tables/charts | Artifacts/events not durable across restart, Engine turns serialized, no token streaming/cancel/retry protocol, no production multi-user auth/rate limits/shared event store. Schema chart support does not mean Engine generates every artifact kind. |

## Explicitly open functional backlog

These identifiers are retained from the publication brief as tracking labels; they are
not newly reproduced failures or a renumbering of unrelated historical F1/F4 audits.

| ID | Open limitation | Disposition |
|---|---|---|
| **U-1** | English per-player paraphrase source-review rejection | Improve semantic abstraction/grounding and conservation; do not add frozen phrase/regex exceptions. |
| **U-2** | Split per-player requests fail closed before execution | Trace Requirement-local clauses, role/output ownership and requested contract binding; not resolved by the existing subject-unit proof path. |
| **F-3/F-5** | Incomplete multi-subject analytical capabilities | Preserve exact output units, scope, population/qualification, dependence and enumeration. Bounded positive proofs are not general comparison/ranking coverage. |
| **F-4** | Forecast execution limitations | Prediction evidence intent is not a forecast execution engine or authority for invented future numbers. |

General natural-language comparative completion remains bounded: trusted typed
`ComparisonContract` permits a narrow GT/LT/EQ scalar proof path; ordinary wording
is not universally converted into such a trusted contract. Existing independent
statistical cells may survive without proving the comparison. No backlog item above
is marked complete by this documentation cycle.

## Baseline / publication posture

The focused identity audit found no confirmed duplicate authoritative domain contract
or material unresolved naming collision. Stale current documentation and implementation
versus accepted-target mismatches were corrected/qualified; see the
[registry findings](../architecture/DOMAIN_CONCEPT_REGISTRY.md#classified-findings-and-disposition).
This is not a claim of full semantic consistency for every possible input.

The publication snapshot is proposed against the verified public source repository
`CityuHK-wyj/baseball_agent` on a branch based on its `main`. Local development
history is intentionally not attached to this branch because the histories are
unrelated. The Pull Request awaits human review and merge.

The local source baseline contained **47 tracked files** with personal absolute paths,
mostly raw historical evidence. The publication branch excludes raw traces and
benchmarks, and replaces two machine-specific paths in the selected source/configuration.
The original local development history is not pushed. This is a review of the proposed
publication tree and its reachable remote history, not a general security certification.
