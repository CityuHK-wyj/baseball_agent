# ADR 0023: Open-world cognition, strict action boundaries

Status: Accepted for the v0.2 open-world runtime.

## Context

The v0.1 dual semantic runtime (ADR 0021, ADR 0022) converted natural-language intent
into a closed, fully typed `SemanticCandidate` before planning could begin. That was the
right response to a real failure class — one model must not both propose and approve
meaning — but it over-constrained cognition. Real-user dogfooding produced ordinary
baseball questions that the closed representation could not express:

- `太鼓达人今年战绩如何？` — a nickname absent from the local dictionary was treated as a
  hard entity failure instead of a routing signal.
- `2023和2025，Freddie Freeman面对高区快速球的EV有什么变化？` — multi-window temporal
  wording raised `ValueError: Multiple date windows require clarification`, which escaped
  to the CLI.
- `今年道奇打者里谁最擅长处理高区快速球？` — "most skilled" is not a predefined metric
  enum, so the request could not be planned.
- `最近30天Ohtani和Judge谁打得更好？` — "better" is not a single metric, so planning
  stalled before it could choose a balanced comparison.
- `DFA是什么意思？` — worked, and must keep working.

The common root cause is that the architecture insisted on SQL-shaped semantics for the
*entire* user query before the system had understood the user's actual goal. Most of that
meaning is not destined for SQL at all: entity resolution, web research and multi-metric
analysis are legitimate work that the closed schema could not represent.

## Decision

Allow partially structured and unstructured representations through the semantic and
planning layers. Require a strict, closed, typed compilation only at privileged
deterministic action boundaries — above all, SQL.

```
User Query
    |
    v
Open-World Semantic Understanding      <-- permissive
    |  structured facts
    |  free-form semantic brief / user goal / planner notes
    |  entities / candidate entities
    |  uncertainties / assumptions / unresolved concepts / search hints
    v
Open-World Planner                     <-- permissive, dynamic tasks
    |  dynamic tasks, free-form objectives
    |  local-data, web-research, entity-resolution, knowledge, comparison tasks
    v
Tool Routing
    +--------------------------+
    |                          |
    v                          v
Knowledge / Web            SQL-capable task
                               |
                               v
                    SQL Analysis Request compilation
                               |
                               v
                    SQLAnalysisRequest (closed, typed)
                               |
                               v
                    deterministic validation + FieldMappingRegistry
                               |
                               v
                    deterministic SQL generation
                               |
                               v
                    SQL security guard -> baseball_readonly
```

## Principle

**Open-world cognition, closed-world execution.**

Be permissive in cognition; be strict at action boundaries.

1. `SemanticUnderstanding` carries free-form fields (`user_goal`, `semantic_brief`,
   `planner_notes`, `analysis_strategy`) alongside high-confidence typed facts. Meaning is
   no longer representable only as enums.
2. The Planner receives the raw query, the semantic understanding, existing evidence,
   available tools/capabilities and budget/retry state. The semantic layer is an
   interpretation aid, not an information bottleneck. Already-confirmed explicit user
   constraints must not be silently contradicted.
3. `UNKNOWN != FAILED`. Unknown entities, nicknames, metrics, baseball terms and local
   coverage gaps are routing information: `UNKNOWN_ENTITY -> EntityResolver -> Shared
   Knowledge -> Web`, `MISSING_LOCAL_DATA -> Web fallback`, and so on.
4. Web is a first-class recovery tool. It returns unstructured evidence; it is never
   forced into Statcast schemas. `Collected != Accepted` remains invariant.
5. `AgentTask` may carry a free-form `objective`, `instructions`, `expected_evidence` and
   `search_hints` in addition to typed structured inputs.
6. `Artifact` may carry unstructured `text_content` alongside its structured payload.
7. Before any PostgreSQL/DuckDB execution, the open-world task is compiled into a strict
   `SQLAnalysisRequest` containing only validated structured information (entities, time
   ranges, metric, aggregation, typed filters, qualification, grouping, limit,
   population). No arbitrary SQL, column names, table names, fragments or LLM-authored
   identifiers.
8. SQL compilation failure returns a structured code (`MISSING_SQL_SEMANTICS`,
   `UNKNOWN_LOCAL_METRIC`, `UNSUPPORTED_LOCAL_ANALYTICS`, `INSUFFICIENT_LOCAL_COVERAGE`)
   to the Planner. It is a planning signal, not necessarily an objective failure.
9. User language must never produce an uncaught semantic/parser exception. Multi-window
   temporal language is interpreted into comparison windows; genuinely open relations are
   assumed-and-disclosed or clarified.
10. Clarification is a last resort: assume when the risk is low and disclose the
    assumption; research when local/web evidence can resolve it; ask the user only when
    the ambiguity materially changes the answer.
11. The independent extractor/reviewer cross-check core goal, explicit constraints,
    entities and material conflicts. Non-material differences in free-form briefs do not
    block execution; material typed contradictions still do.

## Consequences

Benefits:

- ordinary user language works (`太鼓达人`, `谁打得更好`, `2023和2025`, `最擅长`);
- dynamic recovery replaces premature failure;
- Web fallback is a designed path rather than an afterthought;
- fewer meaningless `FAILED`/opaque-UUID outcomes.

Costs and risks:

- greater LLM autonomy in the cognition layer, which is harder to evaluate;
- a more complex Planner and a new recovery/disposition vocabulary;
- a stronger requirement on provenance, Judge quality and the action-boundary security
  gates, because more free-form material now flows toward execution.

## Security invariants (unchanged)

`baseball_readonly`; PostgreSQL read-only transaction; SQL AST/statement guard;
`FieldMappingRegistry`; DuckDB filesystem restrictions; no arbitrary file access; no
secrets in Git; no LLM-generated executable SQL; paid/high-cost permission rules;
Artifact-before-state-reference ordering; crash/restart safety; cross-run/objective
context isolation.

## Authority boundaries

Semantic LLM may produce goal, brief, entity candidates, interpretations, uncertainties,
analysis strategy, planner guidance, search suggestions and free-form descriptions. It
may **not** execute SQL, produce trusted physical fields or unrestricted identifiers,
bypass permissions, accept artifacts, override policy or transition protected state.

Planner may create supporting requirements and tasks dynamically, use free-form
instructions, select a tool class, request web research or local analysis, re-plan after
empty/unsupported results and decide that a vague goal needs multiple metrics. It may
**not** bypass tool permissions, execute SQL directly, bypass `SQLAnalysisRequest`
validation, override rejected evidence, mutate artifact payloads or disable budgets.

## Implementation map

- `app/models/understanding.py` — `SemanticUnderstanding`, recovery codes, analysis
  strategy detection.
- `app/models/sql_request.py` — `SQLAnalysisRequest`, the closed action contract.
- `app/semantic/sql_compiler.py` — open-world requirement -> strict request, structured
  failure codes.
- `app/semantic/normalizer.py` — permissive temporal interpretation, open-world entity
  mention detection, analysis strategy.
- `app/semantic/entity_recovery.py` — local -> shared knowledge -> web entity recovery.
- `app/tools/web_fetch.py` — provider-agnostic search seam and research fetcher.
- `app/models/planning.py` — free-form `AgentTask` fields and recovery stop reasons.
- `app/agent/planner.py`, `app/llm/planner.py` — raw query + understanding + recovery
  signals in Planner context.
- `app/models/artifacts.py` — unstructured `Artifact.text_content`.
- `app/semantic/semantic_validator.py`, `app/models/semantic_candidate.py` — free-form
  interpretation alongside the closed typed constraints.
- `tests/open_world/` — the real-user regression corpus and boundary tests.
