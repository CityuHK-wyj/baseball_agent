# Metric Registry as deterministic knowledge, and run-scoped context isolation

Status: accepted

## Context

D020–D022 require `MetricDefinition` + `SourceMapping` and assign execution correctness
to a Metric Registry while semantic reasoning stays with RAG/Shared Context. D057
forbids a Retrieval Agent. The brief requires cross-run context isolation and warns
against premature embeddings. A concrete, testable source was missing behind
`ContextSource`.

## Decision

- `app/models/metrics.py`: `MetricDefinition` (key, display name, description, required
  data keys, unit) and `SourceMapping` (metric key, source kind, location, DIRECT /
  CALCULATED). `CALCULATED` marks a deterministic Feature Engine computation.
- `app/semantic/metric_registry.py`: `MetricRegistry` with exact `get`/`mapping_for` and
  a deterministic token `search`. Duplicate metric keys and mappings to unknown metrics
  are rejected. It performs no semantic reasoning and no retrieval.
- `app/context/registry_source.py`: `MetricRegistrySource` and `SchemaRegistrySource`
  implement `ContextSource` (`kind="METRIC"` and `kind="SCHEMA"`), so registry
  knowledge reaches the Planner and the Response through the existing Shared Context
  boundary instead of a new agent.
- Run scoping: `ContextItem.scope_run` and `ContextRequest.run_id` isolate per-run
  material. A run-scoped item is returned only when the request's `run_id` matches
  exactly; global knowledge (`scope_run=""`) is visible to every run, and an unscoped
  request sees no run-scoped items. This keeps CompletionReports and private context
  from leaking across runs.
- The Orchestrator now passes `run_id` on both its Planner and Response context
  requests.

## Alternatives

- Putting metric lookup inside the Planner: rejected; it couples planning to physical
  schema and duplicates registry responsibility.
- A Retrieval Agent over a vector store: rejected by D057 and premature.
- Returning another run's CompletionReport as global knowledge: rejected; reports are
  run-scoped and must be retrieved explicitly with the matching run id.

## Consequences

- Metric and schema knowledge is deterministic and testable with no database,
  embeddings or model.
- `scope_run` is a simple exact-match filter; it is not an authorization boundary and
  should not be treated as one.
