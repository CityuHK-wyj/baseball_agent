# v0.1 integration development notes

Recovery now persists `run_definition`, `initial_definition`, `execution_intent` and
reference-only `execution_outcome` records. `RunRecorder.execute_once` claims work atomically
before the external call; a completed execution can be recovered even if its outcome index
was not written. Unknown in-flight outcomes stop with `EXECUTION_UNCERTAIN`. The pipeline
restores each objective through `ResumeService.rehydrate(..., objective_ref=...)`; ambiguous
unscoped multi-objective recovery is rejected. No new AgentState or orchestration service.

`app/runtime.py` is the composition root, exposed by `AnalysisPipeline.default()` and
the CLI. It projects the existing Knowledge Store into EntityDictionary and MetricRegistry,
creates ContextService and SchemaRegistry, configures SourceMappingResolver, and owns local
knowledge/operational connections. Call `pipeline.close()` when finished. Injected stores
remain caller-owned.

Provider seams: `tool_factory` plus `capabilities` for data tools; `web_fetcher`,
`evidence_extractor` and `web_cost` for WebEvidenceTool; `metric_registry`, `schema_registry`,
`knowledge` and `recorder` for configured deployments/tests. SourceMapping applies to
TABLE/FEATURE execution; unregistered or mixed physical mappings fail closed. A
capability's `supported_data_keys` prevents reference knowledge from masquerading as news.

The stored knowledge tool produces EVIDENCE from approved reference-store items. WEB
provenance denotes the original web-reference source; `source=shared-knowledge` explicitly
identifies the local cache. It makes no network request and no analytics measurements.
Only `demo=True` / CLI `--demo` enables synthetic analytics.

Planner context is scoped to the objective's requirements and accepted artifacts. Judge
context is an optional `assess_with_context` extension, preserving existing Judge injection.
ResponsePackage carries bounded knowledge content and provenance, not just knowledge IDs.
RunMetrics writes redacted events individually to OperationalStore through a sink callback.

`RunRecorder.consume_interaction` uses an atomic version comparison before execution.
Reports use `(run, objective)` identity instead of overwriting a single run-level report;
consumers should enumerate `list_objects(kind, run_id)`. Older persisted records remain
readable; automatic migration/recovery of older incomplete runs is not claimed.

Verification: run the full unittest suite, compileall, secret scan and
`scripts/verify_v01_workflows.py`. See [validation](../usage/v01-validation.md) for exact
commands, live results and remaining work. Architecture decisions remain unchanged.
