# Shared Context as a capability with deterministic retrieval

Status: accepted

## Context

D057 and the brief state that retrieval is a Shared Knowledge & Context capability,
not a business sub-agent. D051/D064 require that downstream agents consume accepted
products, not upstream working history, and that the Planner receive bounded indexes
and summaries rather than every payload. The brief also warns against starting with
vector search and embeddings before a deterministic closed loop works.

## Decision

- `app/context/service.py` defines `ContextRequest`, `ContextItem`, `ContextPackage`
  and a `ContextSource` Protocol. `ContextService.retrieve` answers a request from
  registered sources.
- Retrieval is deterministic: filter by `kinds` and `entity_refs`, drop non-accepted
  items, deduplicate by item id, order by `(freshness_rank, kind, item_id)`, then apply
  `max_items` and report `truncated`/`total_available`.
- History is excluded structurally. `ATTEMPT`, `ROUTING_DECISION`, `PLANNER_DRAFT`,
  `JUDGE_REASONING`, `REJECTED_EVIDENCE` and `UNUSED_RAG` are never projected; `PLAN`
  is excluded for the `RESPONSE` purpose only.
- `StaticContextSource` is the first source, over in-memory reference items. A Metric
  Registry, Schema Registry or RAG index can implement the same Protocol later.

## Alternatives

- A separate Retrieval Agent: rejected by D057.
- Vector/embedding retrieval first: deferred until the deterministic loop produces a
  measured benefit; the Protocol allows adding a source without changing callers.
- Projecting raw execution history and filtering in the consumer: rejected; exclusion
  belongs at the retrieval boundary so a consumer cannot accidentally read history.

## Consequences

- No pgvector, embeddings, ranking model or LLM is involved yet.
- `ContextService` is not yet wired into the Planner or Response builder; that wiring
  is a follow-up and should pass only accepted items to the Response purpose.
- The excluded-kind sets are policy surfaces and must be reviewed when new context
  kinds are introduced.
