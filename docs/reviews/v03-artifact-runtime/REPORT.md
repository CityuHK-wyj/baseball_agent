# v0.3 artifact-runtime stop report

Decision: `ARTIFACT_RUNTIME_IMPLEMENTED — READY_FOR_INDEPENDENT_GENERALIZATION_AUDIT`

Status: implementation complete and independently reviewable. Not merged to `main` and not
tagged. Holdout cases were NOT supplied to the implementation.

## Branch / commits

- Branch: `pi/v0.3-artifact-runtime` (created from the audit branch).
- Base: `7c3b6bd` (v0.2 LLM-first runtime) plus the preserved v0.2 audit artifacts.
- Implementation SHA: `10ba81a0f684dedd949f37b8ede6b9b7cff94283` (implementation +
  report commit; the exact audit SHA is the final HEAD recorded in the handoff).
- `main` was not modified during development.

Commit list (before this report commit):

1. `chore(audit): preserve v0.2 anti-shortcut audit report and reproductions`
2. `feat(runtime): artifact-runtime core — envelopes, references, scope, need graph`
3. `feat(runtime): Safe Analytical IR, SchemaCatalog and deterministic SQL compiler`
4. `feat(runtime): composable tool contracts and artifact-driven planner loop`
5. `feat(knowledge): candidate lifecycle, scope and administrator governance`
6. `feat(cli): default to the artifact runtime and add governance commands`
7. `fix(legacy): grounded status, real date windows, authoritative team population`
8. `test(runtime): property-based composition, scope, governance and persistence tests`
9. `docs(runtime): ADR 0025, architecture doc and v0.3 reproductions`

## Architecture diagram

```
Conversation
  → Goal (statement, scope, constraint refs)
  → Need graph (dynamic, dependency ordered)
  → Planner (artifact/dataflow; capability contracts)
  → Tool / Compute
  → Artifact (structured + text + exports + provenance + requested/actual scope + lineage)
  → Artifact references
  → Planner re-evaluation (more needs / more tools)
  → Sufficiency judge (CoverageAssessment)
  → Claims (support_refs)
  → Response composer
```

## Goal model

`Goal(goal_id, statement, scope, constraints, constraint_refs, ambiguity_notes,
source_refs)`. Explicit user constraints are preserved by reference. A new turn starts a
new Goal; prior Goals remain in `previous_goals`, and prior Artifacts remain available for
explicit reuse by reference.

## Need model

`Need(need_id, objective, expected_information, required_scope, preferred_capabilities,
proposed_capability, parameters, input_refs, depends_on, status, criticality,
linked_artifacts, unsatisfied_inputs)`. Objectives are free-form; dependencies and
capabilities are structured; Needs are created dynamically.

## Reference system

`Reference(ref_id, ref_type, target_id, selector, provenance, label)` with types covering
user message/span, conversation message, Artifact, Artifact field/row-set/export, Web
evidence span, Knowledge entry, candidate knowledge, SQL result, derived computation,
planner assumption, clarification answer, tool request/result, claim, goal and need.

## Artifact model / exports

`RuntimeArtifact(artifact_id, kind, structured_data, text_content, exports, provenance,
references, requested_scope, actual_scope, lineage, confidence, status, metadata)`.
Documented export types: `PLAYER_ID_SET`, `TEAM_ROSTER`, `TEAM_ID`, `DATE_RANGE`,
`ENTITY_MAPPING`, `STATISTICAL_RESULT`, `RANKED_ENTITY_SET`, `WEB_EVIDENCE`,
`TRANSACTION_DATE`, `EVENT_SET`, `DERIVED_MEASURE`, `KNOWLEDGE_CANDIDATE`, `SEARCH_QUERY`,
`ANOMALY_SIGNAL` (documented vocabulary, not a closed enum).

## Planner loop

Ready-need selection (dependencies satisfied) → capability-matched tool request →
execution → artifact linkage → coverage assessment → re-plan. Bounded by
`_MAX_ITERATIONS` and per-tool attempts. `add_needs` lets the planner create new Needs from
gaps. Planner failure is not Goal failure.

## Tool capability contracts

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

## Artifact-to-artifact composition

The planner injects available export references into requests whose accepted types match,
so any Artifact export can feed any accepting tool. Demonstrated flows: roster →
`PLAYER_ID_SET` → SQL; Web → `evidence_entities` → `PLAYER_ID_SET` → SQL; SQL → Web
research (the web artifact's lineage records the DB artifact); SQL → Compute → derived
measure. No query-specific direction is coded.

## Safe Analytical IR

`AnalyticalQuery` supports Source, Filter, Entity-set filter, Period/conditional
aggregation, GroupBy, Aggregate, DerivedExpression, Sort, Limit.

Supported aggregations: `COUNT`, `COUNT_NON_NULL`, `COUNT_IF`, `AVG`, `SUM`, `MIN`, `MAX`.
Bounded derived operations: `ADD`, `SUB`, `MUL`, `DIV`, `SAFE_DIV`, `PCT`, `DIFF`, `RATE`.
Conditions: `COMPARE`, `BETWEEN`, `IN`, `NULL`, `AND`, `OR`, `NOT`. Aggregates may be
period-scoped (conditional aggregation). Entity-set filters reference an exported
`PLAYER_ID_SET` and compile to canonical numeric ids.

## SchemaCatalog behavior

`catalog_from_registry()` derives allowed tables/fields/types/meanings/roles/grain/coverage
and operations from the repository's verified `statcast_schema_registry`. Only catalog
fields compile; unknown fields return `UNKNOWN_FIELD`; unsupported operations return
`UNSUPPORTED_OPERATION`; out-of-coverage windows return `INSUFFICIENT_SOURCE_COVERAGE`;
missing entity exports return `MISSING_ENTITY_SET`. Every field used is reported in
`applied_fields` for provenance.

## Canonical metrics vs ad-hoc derived analysis

`MetricRegistry` documents canonical metrics; it does not bound what may be calculated.
Ad-hoc analyses are built from catalog fields through the IR. Live example: hard-hit rate
= `COUNT_IF(launch_speed >= 95) / COUNT_NON_NULL(launch_speed)` on Parquet, and
`AVG(launch_speed)` on PostgreSQL.

## SQL compilation path

`Analysis Need → Safe Analytical IR → SchemaCatalog validation → deterministic compiler →
SQL AST read-only guard → read-only execution`. LLMs never emit SQL, arbitrary
expressions, or unvalidated identifiers.

## Scope preservation

Requested vs actual scope is explicit on Needs and Artifacts. `compare_scope` scores
entity/temporal/population/measure/quality and emits gaps. A broader evidence window is a
gap even when it contains the requested window. Population and measure naming are compared
by meaning (coarse population categories; general measure families), not rigid strings.
Identity-set artifacts (`team_roster`, `entity_mapping`) satisfy membership needs without a
time aggregate.

## Coverage / sufficiency model

`CoverageAssessment` records channel scores, supported claims, missing needs, reasons,
gaps and `core_goal_supported`. `GoalCoverage` summarizes core satisfaction. Completion
requires core coverage, never evidence existence.

## Terminal-state semantics

`RUNNING`, `WAITING_FOR_USER`, `COMPLETE` (core Goal supported), `LIMITED` (useful bounded
answer with gaps), `FAILED` (no useful supported answer).

## Web grounding

Per-finding `grounded` is true only when a page body was retrieved; snippets and search
hits are `PARTIAL` and never support `COMPLETE`. Live page reads in this environment were
blocked because DNS resolved public hosts into a reserved range and the SSRF guard
correctly refused; the guard was not weakened. Grounding behavior is covered by injection
tests, and live search transport works.

## Claim / support-reference design

`Claim(text, support_refs, confidence, scope)`; `validate_claims` keeps only claims whose
support resolves to accepted Artifacts/exports; `unsupported_numbers` flags numeric tokens
not present in supporting evidence.

## Conversation persistence

`RuntimeConversation` persists messages, active Goal, previous Goals, Needs, Artifacts,
References, assessments, decisions, clarifications, accepted context, recent entities and
export refs. `resume_conversation` restores them; a new runtime does not re-run prior work.

## Shared Knowledge new model

READ: planner/semantic/resolver → `KnowledgeStore` → ACTIVE knowledge. WRITE: runtime
discovery → `CandidateKnowledge` → review → administrator → APPROVED/ACTIVE. Candidate
types, scope (domain/language/locale/community/effective dates/authority/source refs),
evidence spans, provenance and conflicts are explicit. Candidates are not
retrieval-authoritative.

## CandidateKnowledge lifecycle

`CANDIDATE`, `UNDER_REVIEW`, `APPROVED`, `ACTIVE`, `REJECTED`, `RETIRED`, `SUPERSEDED`.

## Admin review workflow

```bash
python3 -m app.cli knowledge candidates [--status ...]
python3 -m app.cli knowledge inspect <id>
python3 -m app.cli knowledge approve <id> [--supersede]
python3 -m app.cli knowledge edit-approve <id> --meaning ... [--supersede]
python3 -m app.cli knowledge reject <id> --reason ...
```

## Knowledge conflict handling

`KnowledgeGovernance.conflicts` reports `MEANING_CONFLICT`, `SCOPE_CONFLICT`,
`TEMPORAL_CONFLICT`. Approve without supersede fails closed; supersede marks the existing
item `SUPERSEDED`.

## Migration / deprecated components

Kept and reachable only via `--legacy` / `--demo` / `--empty` and the legacy corpus:
`app/runtime.py`, `app/agent/agent.py`, `app/agent/cognition.py`,
`app/tools/batting.py:select_team`, `app/tools/statcast.py`, `SQLAnalysisRequest`.
Generalized (non-phrase-specific) fixes were applied to that path: grounded status, real
date windows, refusal of shared-city team population, removal of the high-fastball phrase
branch, and snippet non-acceptance. The v0.2 audit reproductions now show the failure
classes addressed while remaining runnable.

## Live status

- PostgreSQL (baseball_readonly, 127.0.0.1:5433): `postgres_aggregate` OK; live
  roster→SQL composition OK (28-player authoritative roster, top avg EV first).
- Parquet (DuckDB, 2015–2023): `parquet_derived_metric` OK.
- Web: search transport OK (LIMITED, 0 grounded); page reads blocked by environment DNS
  resolving public hosts to a reserved range (SSRF guard intentionally not weakened).
- Cross-tool live workflows: roster → PostgreSQL SQL OK.
- Evidence: `docs/reviews/v03-artifact-runtime/live_e2e.jsonl`.

## Test count and categories

- Full suite: `python3 -m unittest discover -s tests -q` → 625 tests, OK.
- New artifact-runtime tests: 59 across
  `tests/artifact_runtime/test_contracts.py`, `test_ir.py`, `test_composition.py`,
  `test_status_and_persistence.py`, `test_governance.py`.
- Categories: references/lineage, scope comparison, sufficiency, IR validation/derived
  calculations/SQL guard, composition (roster→SQL, Web→Entity→SQL, SQL→Web, SQL→Compute,
  replan), terminal states/clarification, persistence/resume, knowledge governance.
- Property-style: parameterized thresholds, windows, limits and id sets produce valid
  read-only SQL.

## Validation commands

```
python3 -m unittest discover -s tests -q          # 625 OK
python3 -m compileall -q app tests docs/reviews   # OK
PYTHONPATH=. python3 docs/reviews/v02-anti-shortcut-audit/reproduce.py   # failure classes fixed
PYTHONPATH=. python3 docs/reviews/v03-artifact-runtime/reproduce.py      # new-runtime checks
PYTHONPATH=. python3 docs/reviews/v03-artifact-runtime/live_e2e.py       # live evidence
```

## Security verdict

All boundaries preserved: `baseball_readonly` role check, read-only transactions,
statement timeouts, SQL AST guard (allowed tables, forbidden functions, DuckDB path
sandbox, quoted-table rejection), SchemaCatalog validation, SSRF guard, credential
redaction, bounded tool budgets, artifact-before-reference ordering, run isolation. The
Safe Analytical IR cannot express arbitrary SQL, Python expressions or unvalidated
identifiers; compiled SQL is re-validated by the guard at execution.

## compileall

`python3 -m compileall -q app tests docs/reviews` → OK.

## Secret scan

`tests/test_secret_scan.py` → OK. No new credentials or literals were introduced; no real
secrets are logged.

## Known architectural limitations

1. Live page reading was unavailable in this environment (DNS/proxy reserved-range
   resolution); grounded web evidence could not be demonstrated live, only by injection
   tests. The SSRF guard was intentionally not weakened.
2. The deterministic (no-LLM) planner produces only knowledge/web Needs; analytical
   composition requires the LLM planner or a scripted planner. This is deliberate
   (no phrase-specific fallback) and yields bounded LIMITED answers.
3. `LLMPlanner` parameter normalization handles capability-shaped keys, but a model that
   emits a structurally different IR will receive a structured recovery code and re-plan
   rather than a silent default.
4. The deprecated legacy path retains its old abstractions for regression compatibility;
   it is not the product path.
5. Historical rosters are not served; the roster capability is authoritative for the
   current roster, and an explicit historical roster need reports a coverage gap.

## Answers to the required questions

1. **Can any Tool Artifact become input to another Tool without a query-specific code
   path?** Yes. Tools declare `accepts`/`produces` export capabilities and the planner
   injects available export references; composition tests cover roster→SQL, Web→Entity→SQL,
   SQL→Web and SQL→Compute.
2. **Can a database analysis use canonical fields to build a derived calculation that is
   not pre-registered as a named metric?** Yes — e.g. hard-hit rate via `COUNT_IF`/ratio
   over `launch_speed`, compiled and executed live on Parquet.
3. **Can requested scope be distinguished from actual evidence scope?** Yes — separate
   `requested_scope`/`actual_scope` and `compare_scope` gaps, including broader-window
   evidence.
4. **Can irrelevant evidence incorrectly make the Goal COMPLETE?** No — completion requires
   core coverage; unrelated artifacts produce `IRRELEVANT`/`PARTIAL` and prevent COMPLETE.
5. **Can an unsupported explicit constraint disappear silently?** No — unsupported IR
   returns structured recovery codes and the planner re-plans; it is not silently dropped.
6. **Can runtime-discovered Web knowledge become ACTIVE Shared Knowledge without
   administrator approval?** No — runtime writes only `CandidateKnowledge`; promotion to
   ACTIVE requires an explicit administrator action.
7. **Can the system trace an important final claim back through derived Artifacts to
   original evidence?** Yes — claims carry `support_refs`; artifacts carry `lineage`; the
   store resolves full upstream lineage; the trace prints claims and support references.
8. **Does any production behavior branch on known dogfooding query literals?** No. The
   high-fastball phrase branch was removed; generic temporal parsing and the SchemaCatalog
   replace known-query handling. The only exact strings in production are tool/capability
   names, HTTP endpoints, source labels and test-injected fakes.

## Independent audit instructions

Review `pi/v0.3-artifact-runtime` at the recorded SHA. Generate holdout queries
independently after inspecting the implementation; do not supply them to Pi in advance.
Treat `docs/reviews/v02-anti-shortcut-audit/reproduce.py` as a regression gate and
`docs/reviews/v03-artifact-runtime/reproduce.py` as class-level evidence, not acceptance.
