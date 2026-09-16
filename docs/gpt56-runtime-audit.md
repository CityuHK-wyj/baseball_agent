# GPT-5.6 Runtime Audit and Hardening Log

Status: IN_PROGRESS

Branch: `gpt56/runtime-audit-hardening`

Baseline: `6675ddd4a37455c46d950bed0aee0f55ac5b6a50`

Ground truth (2026-09-15, Python 3.14.4):

- `python3 -m unittest discover -s tests -v`: 242 tests, 0 failures.
- `python3 -m compileall app`: passed.
- `python3 scripts/secret_scan.py`: passed for the current tree.
- `python3 scripts/secret_scan.py --history`: reports known findings on unrelated old
  local refs. The known unsafe commits are not ancestors of this branch.
- Live PostgreSQL: UNVERIFIED_LIVE.
- Live DuckDB/Parquet: UNVERIFIED_LIVE.
- Architecture blog: direct site access was unavailable in this environment; the audit
  uses the checked-in confirmed decision matrix and ADRs as the frozen architecture.

## Findings

| ID | Severity | Area | Evidence / failing test | Fix | Regression test | Status |
| --- | --- | --- | --- | --- | --- | --- |
| RAH-001 | HIGH | Source Mapping wiring | `Orchestrator.__init__` rejected a resolver and `run` called `Router.route` without an `ExecutionRoute`; Orchestrator-level DIRECT and NO_MAPPING tests failed. | Inject `SourceMappingResolver`, resolve each task from its Requirement data keys, and pass the route into the Router. | `OrchestratorSourceMappingTests` | FIXED |
| RAH-002 | HIGH | Persistence run isolation | Saving the same `(kind, object_id)` for Run B silently overwrote Run A because the operational schema uses that pair as its global identity. | Reject cross-run identity reassignment before the upsert; same-run idempotent/versioned saves remain valid. | `OperationalStoreTests.test_object_identity_cannot_be_reassigned_to_another_run` | FIXED |
| RAH-003 | CRITICAL | Cross-objective evidence isolation | An accepted assessment with `objective_ref=None` for Objective A was included in Objective B's final artifacts and response because projection filtered only by objective. | Scope both completion and response assessments to the current objective's Requirement IDs, while retaining compatibility with legacy unscoped assessments for those same requirements. | `CrossObjectiveEvidenceIsolationTests` | FIXED |
| RAH-004 | HIGH | Observability redaction | The test claiming whole-event safety placed its secret only in `message`; `run_id`, `subject_ref`, `agent`, `status`, `source`, and `tool` stored secrets verbatim. | Redact the run id at collector construction and every textual event field at the recording seam. | `RunMetricsTests.test_no_stored_field_contains_the_known_value` | FIXED |
| RAH-005 | MEDIUM | Runtime observability wiring | `RunMetrics` existed as an isolated collector, but an actual Orchestrator run emitted no events and exposed none to its caller. | Emit redacted lifecycle events from the Orchestrator and return them with `RunResult`; include duration, plan/replan, routing, task/attempt, artifact/reuse, assessment and final status. | `OrchestratorEndToEndTests.test_run_emits_metrics_for_the_full_runtime_lifecycle` | FIXED |
| RAH-006 | HIGH | Web evidence runtime chain | Raw-web extraction could not execute through the runtime: final evidence had an unregistered lineage parent and an extractor-only descriptor that failed semantic validation. | Add `WebEvidenceTool`, register the raw page only as a supporting internal artifact, project the requirement descriptor onto extracted evidence, and persist/register the support before the final artifact. | `WebEvidenceToolTests` | FIXED (injected fetcher; live provider remains unverified) |
| RAH-007 | CRITICAL | DuckDB filesystem guard | File reader lists, URIs, dynamic reader arguments, and DuckDB quoted file-table syntax passed the guard and opened a connection outside the archive root. | Require static literal paths (including literal arrays), reject URIs, and reject quoted DuckDB table expressions before connect. | `DuckDBGuardTests.test_paths_outside_the_root_are_rejected_before_connect` | FIXED |
| RAH-008 | HIGH | LLM evidence grounding | `LLMEvidenceExtractor` accepted arbitrary non-empty model claims and ignored unknown fields, allowing fabricated facts into Evidence. | Require a bounded, closed JSON schema and require every claim and support string to occur in the raw page; invalid output uses the deterministic fallback when configured. | `LLMEvidenceExtractorTests.test_hallucinated_claim_falls_back_to_grounded_extraction` and `test_extra_fields_are_rejected` | FIXED |
| RAH-009 | HIGH | Persisted planner terminal lifecycle | Resume recreated a terminal latch from the current external condition, so a newly permitted source stayed blocked; it also did not clear the latch before calling Planner. | Store the terminal-condition fingerprint in checkpoints, compare it on resume, and observe a changed condition before building `PlannerContext`. Legacy checkpoints retain terminal behavior when no fingerprint exists. | `OrchestratorPersistenceTests.test_new_permitted_source_reopens_a_persisted_noncomplete_terminal_plan` | FIXED |
| RAH-010 | HIGH | LLM Planner schema boundary | Unknown output fields were discarded and a non-array `tasks` value raised an internal `AttributeError`, bypassing the configured deterministic fallback. | Enforce bounded closed decision/task schemas and explicit field types; normalize all invalid model shapes to validation failures. | `LLMPlannerTests.test_unknown_output_fields_are_rejected` and `test_wrong_task_shape_falls_back_instead_of_raising_an_internal_error` | FIXED |
| RAH-011 | HIGH | SourceMapping physical capability | Router enforced only the mapped source kind; a Planner preference could select a different tool within that same source kind. | Treat `ExecutionRoute.tool` as a hard mapping constraint and block all other tools before applying Planner preference or optimization. | `RouterSourceMappingTests.test_direct_route_enforces_the_mapped_tool_within_a_source_kind` | FIXED |
| RAH-012 | HIGH | Clarification runtime lifecycle | Ambiguous semantic output returned directly to the caller, with no run id, durable request, checkpoint, confirmation path, or replay protection. | Persist a small interaction record and `WAITING_FOR_USER` checkpoint; resume the same objective id after a validated answer, attach a `USER_CONFIRMED`/`USER_CONSTRAINT` entity constraint, and reject replayed answers before execution. | `EndToEndTests.test_clarification_is_checkpointed_and_resumes_the_same_run` | FIXED |
| RAH-013 | HIGH | Permission lifecycle and system-policy boundary | Paid capability was indistinguishable from a forbidden capability, and a cost approval could have enabled every tool at that cost. | Persist a scoped `PermissionRequest`; approve only its tool/cost in a run-local Router copy; model system-forbidden capabilities separately and exclude them from both consent and routing. | `EndToEndTests.test_paid_source_permission_is_checkpointed_scoped_and_single_use`; `RouterTests.test_user_cost_approval_cannot_enable_a_system_forbidden_tool`; `test_permission_scope_does_not_enable_another_paid_tool` | FIXED |

## Open audit areas

- Persistence crash consistency, checkpoint completeness, and partial-resume idempotency.
- Cross-run/cross-objective assessment and ResponsePackage isolation.
- Web result → Evidence → EVIDENCE Artifact runtime wiring.
- Orchestrator RunMetrics emission and redaction of every text field.
- SQL/DuckDB parser and filesystem bypass battery.
- LLM output schema, size, partial-response and hallucination guards for judge and
  response composition.
- Permission and constraint-revision workflows.
- Realistic baseball E2E and CLI/documentation truthfulness.
# Latest v0.1 checkpoint (2026-09-16)

335 tests pass; compileall, secret scan and separate-process CLI workflows pass.
Additional regressions fixed: reused run IDs overwrite prior work, multi-objective report
overwrite, inaccessible response payload references, mixed-source partial routing, and
current snapshots being treated as historical facts without effective dates. One original
refresh test depended on the calendar date; it now verifies the actual verification time.
The default paid Web seam is tested through rejection, approval and repeated-answer rejection.
Full-name entity resolution now suppresses overlapping short aliases: Aaron Judge no longer
triggers an unrelated Hank Aaron clarification during the README demo command.

Remaining work and exact next task are in `docs/codex-handoff.md`; live probe details are
in `docs/usage/v01-validation.md`. Status remains IN_PROGRESS — stable checkpoint.

# Earlier v0.1 integration checkpoint (2026-09-15)

Runtime `b68ccf7` and Shared Knowledge `7d44ef4` are merged on
`astra/v0.1-integration`. Combined suite: 320 tests passing.
Permission requests expire after 15 minutes, revalidate current capability and system
policy, and grant execution only to the requested objective. Interaction consumption
uses an atomic version check before execution; immutable audit snapshots are persisted.
Source `USER_CONSTRAINT` revisions are checkpointed before execution; acceptance records
`USER_CONFIRMED`, rejection preserves the original restriction, and replay is rejected.
System source policy cannot be revised. Clarification resume now passes through the same
constraint and permission checks.

Next checkpoint: 324 tests passing. Planner indexes and assessment summaries now exclude
other objectives/runs; only accepted artifacts enter the Planner index. Natural bilingual
questions retrieve embedded knowledge aliases. Context requests accept historical dates;
ResponsePackage now carries bounded knowledge content with authority, verification and
freshness metadata. RunMetrics events persist individually to OperationalStore after redaction.

Remaining: default pipeline composition, runtime date extraction, live verification,
and crash recovery after interaction consumption.

Default composition now lives in `app/runtime.py`, exposed as `AnalysisPipeline.default()`.
Knowledge is the canonical source for entity and metric projections. A bounded stored-web
knowledge evidence tool answers definitions without synthetic analytics; synthetic execution
requires `--demo`. The Router checks supported data keys, so knowledge definitions cannot
mask a paid injury/news source. Context is projected to contextual Judges with the hard veto
unchanged. Explicit dates and single-season years filter runtime knowledge; a bare year uses
July 1 and multi-year historical comparison remains unfinished.

Live probe: local 2023 Parquet bounded reads pass. High-zone query lacks `sz_bot/sz_top`.
MLB StatsAPI returned 30 teams on one attempt, then timed out on a repeat; this verifies
transport once, not the WebEvidenceTool end-to-end. PostgreSQL localhost:5433 refused the
connection; no Operational PostgreSQL connection is configured.
