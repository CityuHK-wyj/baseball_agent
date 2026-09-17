# End-to-end pipeline, CLI and usage documentation

Status: accepted

## Context

The brief requires a runnable system and documentation good enough that a newcomer can
install, configure, run, query, resume, test and extend it from the README and
`docs/usage/`. The pieces existed but were not composed into a runnable entry point, and
there was no end-to-end test or user documentation.

## Decision

- `app/tools/synthetic.py`: `SyntheticDataTool` fabricates an Artifact matching a
  Requirement's descriptor and is explicitly `SYNTHETIC`. It lets the whole pipeline run
  offline, without a database or credentials.
- `app/pipeline.py`: `AnalysisPipeline` composes semantic normalization → requirement
  decomposition → per-objective Orchestrator runs → CompletionReport/ResponsePackage →
  response text. It returns a `PipelineResult`, or a clarification request when intent is
  ambiguous (it never guesses). A `tool_factory` seam lets callers swap the data source.
- `app/cli.py`: `ask`, `resume`, `inspect`, `show-artifact` and `metrics` subcommands.
  Offline by default; `--persist` writes through `RunRecorder`; `--json` for machines.
- `tests/integration/test_end_to_end.py`: raw query → … → response, plus clarification,
  per-objective response scoping, the empty-source failure path, and persisted
  checkpoint/resume.
- Docs: rewritten `README.md`; added `docs/usage/{quickstart,configuration,databases,running,examples,security,troubleshooting}.md`
  and `docs/development/{architecture,adding-a-metric,adding-a-source}.md`. Every
  documented CLI command was executed to verify it.

## Bug found and fixed

With services shared across objectives, `ResponsePackage` (and the `CompletionReport`)
included accepted assessments from *other* objectives. `build_response_package` and the
Orchestrator now scope accepted evidence to the current `objective_ref`, and the
integration test asserts one accepted evidence per objective. This was a real
accepted-product boundary leak.

## Alternatives

- A dashboard/web UI: rejected; a CLI plus a programmatic API is enough for v1.
- Requiring a live database for the default path: rejected; it would make the project
  unrunnable on a clean checkout.

## Consequences

- The project is runnable and documented end to end offline; the E2E test is deterministic
  and needs no network.
- The synthetic source is a demonstration fixture, not real data; the response labels it
  `from synthetic`.
- The pipeline does not yet wire `SourceMappingResolver` per task or pass a live DB tool;
  that remains a documented follow-up.
