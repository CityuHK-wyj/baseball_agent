# ADR 0024: LLM-first cognition, deterministic execution

Status: Accepted for the v0.2 LLM-first runtime.

## Context

ADR 0023 introduced an open-world semantic layer but still kept semantic parsing on the
critical path: a fully typed `SemanticCandidate` had to be produced before planning could
continue, and a dual-LLM reconciliation gate could still turn "the two readers phrased it
differently" into a clarification. Real dogfooding showed the remaining problems were not
parser bugs but architecture:

- a semantic failure still stopped planning even when the Planner could have researched;
- vague goals ("谁打得更好") had no metric and stalled;
- unknown cultural references ("太鼓达人") were treated as errors rather than research tasks;
- team-level and recent-window questions had no tool and terminated;
- answers exposed internal artifacts, requirements and ids;
- clarification was a CLI state machine requiring `--run-id/--request-id/--choice`.

The system was still asking the user to speak the database's language.

## Decision

Make the LLM the primary cognition layer and move strictness to action boundaries.

```
User Message
  -> Conversation / Request Context
  -> LLM Understanding + Planner (free-form intent, entities, needs, strategy,
       research queries, analytical hints, optional clarification)
  -> Tools: Shared Knowledge | Web research | Batting/Pitching stats | Local analytics
  -> strict SQLAnalysisRequest only for local analytics
  -> Evidence loop / bounded recovery re-plan
  -> LLM Response Composer
  -> natural answer, or WAITING_FOR_USER clarification
```

1. **Semantic parsing is no longer a terminal gate.** The Planner always receives the raw
   message plus whatever partial understanding exists. `SEMANTIC_UNAVAILABLE` is a
   recovery signal, never a terminal state.
2. **Dual semantic review is demoted.** The compatibility check exists to catch material
   explicit contradictions (2024 vs 2025, Judge vs Ohtani, MAX vs AVG when stated, an
   explicit ALL_PITCHES population). It is not a second rules engine and does not block on
   compatible rephrasing.
3. **Raw user language is authoritative.** Cognition components retain the original
   wording; free-form strings (`user_goal`, `semantic_brief`, `analysis_strategy`,
   `task_objective`, `instructions`, `expected_evidence`, `search_query`, `judge_summary`,
   `answer_context`) coexist with typed hints.
4. **Unknown -> investigate.** Unknown nicknames, cultural references, terms, metrics and
   coverage gaps are routing signals. The Planner may run web research, entity resolution,
   shared knowledge or another metric.
5. **Web is a normal tool.** A live keyless search (DuckDuckGo Lite with a Bing fallback)
   plus bounded page reading returns unstructured evidence with provenance. It is not
   forced into a SQL schema and is never auto-promoted into authoritative knowledge.
6. **Strictness begins at compilation.** A local analysis hint is compiled into a closed
   `SQLAnalysisRequest`. Only trusted registries supply metric/location/population names.
   SQL compilation failure returns a structured recovery code to the Planner.
7. **The LLM never emits executable SQL**, physical columns, table names or paths. The
   deterministic SQL builder and AST guard remain the only path to the database.
8. **Clarification is first-class and conversational.** `WAITING_FOR_USER` is a status,
   not a failure. A clarification is asked only when the answer materially changes and no
   reasonable default exists; the user answers naturally in `chat`.
9. **Conversation is a real session.** The application service retains messages, pending
   clarification, recent entities and accepted context; follow-ups such as "那去年呢？"
   resolve against the previous turn.
10. **Candidate knowledge is governed.** Runtime discoveries are filed as
    `CandidateKnowledge` with status `CANDIDATE`. Promotion to ACTIVE Shared Knowledge is
    an explicit administrative action (`knowledge review --approve --ingest`).

## Principle

**Flexible cognition, deterministic execution.**

The LLM may think, understand, plan, research and explain. Deterministic code intervenes
primarily when the system is about to perform a privileged or correctness-critical action.

## Consequences

Benefits: normal Chinese/English questions work; unknown references are researched; vague
comparisons get a reasonable multi-indicator plan; web and batting/pitching capabilities
close real gaps; answers are natural; conversations support follow-ups.

Costs: more LLM calls (latency/cost), harder evaluation, and a stronger need for evidence
provenance and action-boundary tests. Live search quality varies and must be disclosed.

## Security invariants (unchanged)

`baseball_readonly`; PostgreSQL read-only transactions; SQL AST guard; typed
`SQLAnalysisRequest`; `FieldMappingRegistry`; DuckDB path sandbox; bounded HTTP with an
SSRF guard; no LLM-authored SQL; permission/budget policies; artifact-before-state
ordering; crash recovery; run/context isolation.

## Implementation map

- `app/agent/agent.py` — `BaseballAgent` application service and turn loop.
- `app/agent/cognition.py` — LLM plan/compose prompts, deterministic fallback.
- `app/agent/factory.py` — composition root.
- `app/agent/local_analytics.py` — hint -> strict `SQLAnalysisRequest` -> read-only SQL.
- `app/tools/web_research.py` — live search + bounded page reading.
- `app/tools/batting.py`, `app/tools/pitching.py` — live batting/pitching lines.
- `app/tools/entity_lookup.py` — local -> MLB registry -> evidence resolution.
- `app/knowledge/candidates.py` — candidate knowledge governance.
- `app/models/agent_runtime.py` — conversation, plan, evidence and trace models.
- `app/cli.py` — `chat`, LLM-first `ask`, `--trace`, `--legacy`, candidate review.
