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

## Open audit areas

- Persistence crash consistency, checkpoint completeness, cross-run identity isolation,
  and partial-resume idempotency.
- Cross-run/cross-objective assessment and ResponsePackage isolation.
- Web result → Evidence → EVIDENCE Artifact runtime wiring.
- Orchestrator RunMetrics emission and redaction of every text field.
- SQL/DuckDB parser and filesystem bypass battery.
- LLM output schema, size, partial-response and hallucination guards for planner,
  judge, and response composition.
- Clarification/permission/constraint-revision workflows.
- Realistic baseball E2E and CLI/documentation truthfulness.
