# Artifact Runtime (v0.3)

> Flexible cognition, composable evidence, deterministic actions.

The artifact runtime is the center of Baseball Agent v0.3. It replaces the
`SemanticCandidate` / `Requirement -> Tool` center with a Goal → Need graph → Planner →
Tool → Artifact → references → re-plan → Sufficiency judge → Response loop.

Code lives under `app/artifact_runtime/`, contracts under `app/models/artifact_runtime.py`.
The decision record is `docs/adr/0025-artifact-runtime.md`.

## Architecture

```
Conversation
   ↓  ReferenceStore (user message, spans, conversation messages)
Goal
   ↓  Need graph (dynamic, dependency-ordered)
Planner  ──────────────────────────────────────────────┐
   ↓  ToolRequest (objective + input_refs + structured) │ re-evaluation
Tool / Compute                                          │
   ↓                                                     │
Artifact (exports + provenance + requested/actual scope)│
   ↓  Artifact references                                │
Planner re-evaluation ───────────────────────────────────┘
   ↓
Coverage / Sufficiency judge
   ↓
Claims (support_refs)
   ↓
Response composer
```

## Goal model

`Goal` = the user's desired outcome (`statement`), an optional `scope`, explicit
`constraints` preserved by reference (`constraint_refs`), and `ambiguity_notes`. A new
turn starts a new Goal; prior Goals are retained in `previous_goals` and prior Artifacts
remain available for explicit reuse by reference.

## Need model

`Need` = information still required to satisfy the Goal. `objective` and
`expected_information` are free-form; `required_scope`, `preferred_capabilities`,
`proposed_capability`, `parameters`, `input_refs`, `depends_on`, `criticality` and
`status` are structured. Needs are created dynamically at runtime and may depend on other
Needs.

## Reference system

`Reference` points back to original information: user message, user span, conversation
message, Artifact, Artifact field/row-set, Artifact export, Web evidence span, Knowledge
entry, candidate knowledge, SQL result, derived computation, planner assumption,
clarification answer, tool request/result, claim, goal or need. References avoid
`summary → summary → summary` loss; new interpretations reference originals.

## Artifact model and exports

`RuntimeArtifact` carries `structured_data`, `text_content`, `exports`, `provenance`,
`references`, `requested_scope`, `actual_scope`, `lineage`, `confidence` and `status`.
Exports are reusable machine-consumable outputs. Documented types include
`PLAYER_ID_SET`, `TEAM_ROSTER`, `TEAM_ID`, `DATE_RANGE`, `ENTITY_MAPPING`,
`STATISTICAL_RESULT`, `RANKED_ENTITY_SET`, `WEB_EVIDENCE`, `TRANSACTION_DATE`,
`EVENT_SET`, `DERIVED_MEASURE`, `KNOWLEDGE_CANDIDATE`, `SEARCH_QUERY`, `ANOMALY_SIGNAL`.
The list is a documented vocabulary, not a closed enum.

## Planner loop

Each iteration: choose a ready Need (dependencies satisfied), pick a tool by capability
contract, build a `ToolRequest` (objective + `input_refs` + structured inputs), execute,
link Artifacts to the Need, assess coverage, and repeat while recoverable, budget remains,
and useful actions exist. Planner failure is not Goal failure. `LLMPlanner` creates Needs
from the goal; `DeterministicPlanner` is a generic capability planner with no
phrase-specific analysis. `ScriptedPlanner` exists for embedding/tests.

## Tool capability contracts

Each tool declares `accepts` and `produces` export capabilities:

| tool | accepts | produces |
|---|---|---|
| `shared_knowledge` | `SEARCH_QUERY` | `KNOWLEDGE_CANDIDATE` |
| `web_research` | `SEARCH_QUERY`, `ENTITY_CONTEXT`, `STATISTICAL_RESULT`, `ANOMALY_SIGNAL` | `WEB_EVIDENCE` |
| `entity_resolution` | `ENTITY_MENTION` | `ENTITY_MAPPING`, `PLAYER_ID_SET` |
| `evidence_entities` | `WEB_EVIDENCE`, `KNOWLEDGE_CANDIDATE` | `ENTITY_MAPPING`, `PLAYER_ID_SET` |
| `roster` | `TEAM_NAME`, `SEARCH_QUERY` | `TEAM_ROSTER`, `PLAYER_ID_SET` |
| `batting_stats` | `PLAYER_NAME`, `PLAYER_ID_SET`, `SEASON`, `DATE_RANGE` | `STATISTICAL_RESULT`, `RANKED_ENTITY_SET` |
| `local_analytics` | `PLAYER_ID_SET`, `DATE_RANGE`, `ANALYTICAL_QUERY` | `STATISTICAL_RESULT`, `RANKED_ENTITY_SET`, `DERIVED_MEASURE` |
| `compute` | `STATISTICAL_RESULT`, `DERIVED_MEASURE`, `RANKED_ENTITY_SET` | `DERIVED_MEASURE`, `STATISTICAL_RESULT`, `RANKED_ENTITY_SET` |

Composition is by capability, so Web → SQL, SQL → Web, Knowledge → SQL, SQL → Compute →
Web, Web → Entity Resolution → SQL are all ordinary flows, not workflows.

## Safe Analytical IR

`AnalyticalQuery` supports Source, Filter, Entity-set filter, Period / conditional
aggregation, GroupBy, Aggregate, DerivedExpression, Sort and Limit. Aggregations:
`COUNT`, `COUNT_NON_NULL`, `COUNT_IF`, `AVG`, `SUM`, `MIN`, `MAX`. Bounded derived
operators: `ADD`, `SUB`, `MUL`, `DIV`, `SAFE_DIV`, `PCT`, `DIFF`, `RATE`. Conditions are
structured (`COMPARE`, `BETWEEN`, `IN`, `NULL`, `AND`, `OR`, `NOT`). Arbitrary SQL
fragments, Python expressions and unvalidated identifiers are impossible to express.

## SchemaCatalog behavior

`SchemaCatalog` exposes allowed tables, fields, types, meanings, roles, nullability,
entity relationships, grain, coverage and allowed operations, built from the repository's
verified source metadata (`statcast_schema_registry`). Only catalog fields are valid;
hallucinated fields return `UNKNOWN_FIELD`. Field selection can be provenance-tracked via
`applied_fields` on the compiled result.

## Canonical metrics vs ad-hoc derived analysis

`MetricRegistry` documents canonical reusable baseball metrics; it no longer bounds what
may be calculated. Ad-hoc analyses are built directly from catalog fields through the IR
(for example `COUNT_IF(launch_speed >= 95) / COUNT_NON_NULL(launch_speed)`).

## SQL compilation path

```
Analysis Need
→ Safe Analytical IR
→ SchemaCatalog validation
→ deterministic compiler (app/artifact_runtime/ir_compiler.py)
→ SQL AST read-only guard
→ read-only execution (baseball_readonly / DuckDB sandbox)
```

## Scope preservation and coverage

Every Need and Artifact can describe `Scope` (entities, population, time range, season,
game types, metric, event population, source coverage). `compare_scope` compares requested
vs actual and produces explicit gaps. A broader evidence window is a gap even when it
contains the requested window. `CoverageAssessment` records entity/temporal/population/
measure/quality channels, supported claims, missing needs and `core_goal_supported`.

## Terminal-state semantics

`RUNNING`, `WAITING_FOR_USER`, `COMPLETE` (core Goal supported), `LIMITED` (useful
bounded answer with gaps), `FAILED` (no useful supported answer). COMPLETE is never
implied by evidence existence.

## Web grounding

Web artifacts preserve URL, title, retrieved timestamp, snippet, retrieved page body and
per-finding `grounded` state. A search hit (snippet) is never grounded support; only a
retrieved page body is accepted. The judge distinguishes search hit, retrieved evidence
and supported claim.

## Claims and support references

`Claim(text, support_refs, confidence, scope)` is validated so every support reference
resolves to an accepted Artifact or export. `unsupported_numbers` surfaces numeric tokens
in a claim that do not appear in supporting evidence.

## Conversation persistence

`RuntimeConversation` persists messages, active Goal, previous Goals, Needs, Artifacts,
References, assessments, planner decisions, clarifications, accepted context, recent
entities and export references. Restart restores lineage and evidence without re-running
expensive work.

## Shared Knowledge governance

- READ PATH: planner / semantic / entity resolver → `KnowledgeStore` → ACTIVE knowledge.
- WRITE PATH: runtime discovery → `CandidateKnowledge` → review queue → administrator →
  APPROVED/ACTIVE.

`CandidateKnowledge` carries type, scope (domain/language/locale/community/effective
dates/authority/source refs), evidence spans, provenance, originating run and conflicts.
Statuses: `CANDIDATE`, `UNDER_REVIEW`, `APPROVED`, `ACTIVE`, `REJECTED`, `RETIRED`,
`SUPERSEDED`. Knowledge types distinguish `CANONICAL_FACT`, `ENTITY_ALIAS`, `DEFINITION`,
`HISTORICAL_EVENT`, `RULE`, `COMMUNITY_REFERENCE`, `COMMUNITY_OPINION`, `SCOUTING_NOTE`,
`TACTICAL_CONCEPT`, `SOURCE_INTERPRETATION` and `CONTEXT_REFERENCE`. A candidate is never
retrieval-authoritative; a confidence threshold is not approval.

## Administrator review workflow

```bash
python3 -m app.cli knowledge candidates [--status CANDIDATE]
python3 -m app.cli knowledge inspect <candidate_id>
python3 -m app.cli knowledge approve <candidate_id> [--supersede]
python3 -m app.cli knowledge edit-approve <candidate_id> --meaning ... [--supersede]
python3 -m app.cli knowledge reject <candidate_id> --reason ...
```

## Knowledge conflict handling

`KnowledgeGovernance.conflicts` reports `MEANING_CONFLICT`, `SCOPE_CONFLICT` and
`TEMPORAL_CONFLICT` against ACTIVE knowledge. Approval without `supersede` fails closed;
`supersede` marks the conflicting item `SUPERSEDED` rather than overwriting silently.

## Migration and deprecated components

Staged migration kept the legacy path runnable: `app/runtime.py` (requirement pipeline),
`app/agent/agent.py` (`BaseballAgent`), `app/agent/cognition.py`
(`DeterministicCognition`), `app/tools/batting.py:select_team`, and the legacy
`app/tools/statcast.py` + `SQLAnalysisRequest` boundary. These are reachable only via
`--legacy` / `--demo` / `--empty` and the legacy pytest corpus. Generalized fixes applied
to that path: grounded-evidence status, relative/explicit date windows, refusal of
shared-city team population, removal of the phrase-specific high-fastball branch, and
snippet non-acceptance.

## Testing philosophy

Unit tests cover IR validation, reference resolution, artifact lineage, scope comparison,
knowledge governance, SQL security and state persistence. Capability acceptance tests
properties (see `tests/artifact_runtime/`), not known sentences. The preserved v0.2 audit
reproductions are regression gates; the v0.3 harness
(`docs/reviews/v03-artifact-runtime/reproduce.py`) demonstrates the fixed behavior on
problem classes.
