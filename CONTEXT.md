# Baseball Agent Domain Context

This document defines the shared vocabulary for the repository. Update it when a domain term is clarified or introduced; keep implementation details in code and durable architecture choices in ADRs.

## Purpose

Baseball Agent answers baseball analytics questions by selecting an appropriate data source, retrieving data, validating the result, computing derived features when needed, and formatting a user-facing response.

## Glossary

- **Analytics request**: A user's question about baseball performance, players, teams, seasons, or pitch events.

**Semantic Candidate**: The closed, typed proposal an extractor emits for one analytics
request: canonical metrics, operators, aggregation, count states, population and
qualification, each with evidence. It is not executable semantics; the semantic validator
must accept it before it can reach planning.

**Semantic Provenance**: The evidence text (and optional source offsets) connecting one
extracted constraint back to the user's query. Reused by numeric-ownership checks,
contradiction detection and audit.

**Semantic Validator**: The deterministic authority that validates a Semantic Candidate
against the closed vocabulary, grounds its evidence, reconciles it against narrow lexical
anchors, detects contradictions and numeric cross-binding, and only then emits canonical
typed constraints. It enforces closed-world invariants, not open-world language
comprehension.

**Lexical Anchors**: The narrow, high-confidence deterministic facts of a raw query
(explicit years/dates, numeric literals with units, count literals, ranking limits,
population wording, known pitch families, explicit location definitions). They are
supporting evidence used to reject a contradiction or a silently dropped explicit
restriction; they are not a natural-language parser and not the complete meaning.

**Semantic Extractor (LLM A)**: The role that independently proposes a Semantic Candidate
from the raw query. It never reviews or approves its own candidate, and never sees
physical schema or SQL.

**Semantic Reviewer (LLM B)**: The independent role that reconstructs the query's meaning
without being shown Candidate A. It exists so that no single model both proposes and
approves semantic meaning.

**Semantic Reconciler**: The deterministic stage that compares Candidate A, Candidate B
and the lexical anchors. Non-material differences reconcile automatically; material
differences become a Clarification and the user is the semantic authority. No third model
votes.

**Semantic Review Result**: The bounded, durable artifact recording both candidates, the
agreement status, material differences, ambiguities, models and latency, and the canonical
candidate only when execution is safe. It never contains hidden model reasoning, prompts,
SQL or credentials.

**Dual Semantic Runtime**: The pipeline stage combining lexical anchors, the independent
extractor and reviewer, the Semantic Reconciler, the deterministic Semantic Validator and
fail-safe provider-failure policy. It is the only source of analytical constraints for an
objective; a single model may never both propose and approve meaning.

**Open-World Semantic Understanding**: The v0.2 permissive interpretation of one request.
It keeps high-confidence typed facts (entities, time hints, constraints) and adds
first-class free-form meaning (`user_goal`, `semantic_brief`, `planner_notes`,
`analysis_strategy`) plus uncertainties (unresolved concepts, search hints, recovery
codes). It is an interpretation aid for the Planner, never executable semantics; only the
SQL action boundary is closed and typed.

**Recovery Code**: A structured signal that a concept is unknown or local coverage is
insufficient (`UNKNOWN_ENTITY`, `UNKNOWN_METRIC`, `MISSING_LOCAL_DATA`, …). It routes work
to entity resolution, Shared Knowledge, web research or re-planning. The governing
invariant is `UNKNOWN != FAILED`.

**Analysis Strategy**: A bounded, documented multi-indicator plan for a vague analytical
goal such as “better”, “most skilled” or “recent form”. It exists so a natural concept is
not forced into a single predefined metric.

**SQLAnalysisRequest**: The strict, closed, typed contract required before any privileged
PostgreSQL/DuckDB execution. It contains only validated structured information (entities,
time ranges, metric, aggregation, typed filters, qualification, grouping, limit,
population) and no arbitrary SQL, identifiers or fragments. Open-world plans are compiled
into it; a compilation failure returns a recovery code to the Planner.

**Free-Form Task Objective**: A natural-language `objective`/`instructions`/
`expected_evidence` on an `AgentTask`, alongside typed structured inputs. It lets the
Planner express web-research, entity-resolution or multi-metric work without collapsing
the task into fixed enum values.

**Unstructured Artifact Content**: The `text_content` an `Artifact` may carry alongside its
structured payload (for example extracted web text). The Judge decides whether it
satisfies a Requirement; `Collected != Accepted` remains invariant.

**LLM-First Runtime**: The v0.2 conversational architecture in which the LLM is the
primary cognition layer (understanding, planning, research, explanation) and strictness
is applied only at privileged action boundaries. Semantic parsing is an input to the
Planner, never a mandatory gate; `UNKNOWN` is a routing signal, not a failure.

**Conversation Session**: The mutable application-service state for one user session:
messages, current status, pending clarification, recent entities and accepted context.
Follow-ups such as “那去年呢？” resolve against this context.

**Run Status**: The user-facing lifecycle state — `PENDING`, `RUNNING`, `WAITING_FOR_USER`,
`COMPLETE`, `LIMITED`, `FAILED`. `WAITING_FOR_USER` is a first-class state for a genuine
clarification, never `FAILED`.

**Candidate Knowledge**: A runtime discovery (for example a community nickname found by web
research) recorded with provenance and status `CANDIDATE`. Runtime agents cannot promote
it into authoritative Shared Knowledge; an administrator must approve it.

**Live Web Research Tool**: A first-class Planner tool that performs real public search and
bounded page reading, returning unstructured, sourced evidence. It is not forced into a SQL
schema and is not auto-promoted into Shared Knowledge.

**Batting/Pitching Stats Tool**: Live capability returning season or date-range rate stats
(PA, AVG, OBP, SLG, OPS, HR, BB, SO; W-L, ERA, WHIP, SO, IP) from a live source, so vague
“who is better / why is he strong” questions have real numbers.
- **Baseball Agent**: The tool-calling application that interprets an analytics request and coordinates data retrieval and response generation.
- **Hot data**: Recent Statcast data stored in PostgreSQL. The current configured coverage is 2024–2026.
- **Cold data**: Historical Statcast data stored as Parquet and queried through DuckDB. The current configured coverage is 2015–2023.
- **Live leaderboard data**: Season batting or pitching summaries retrieved through the pybaseball/FanGraphs adapter when precomputed metrics such as WAR, wRC+, ERA, or OPS are required.
- **Player dictionary**: The mapping between MLBAM player identifiers and human-readable player names.
- **Batting snapshot**: Derived batting statistics for an inclusive date range, including batting average, on-base percentage, slugging percentage, and OPS.
- **Pitch heatmap**: A strike-zone visualization derived from pitch-location coordinates for a pitcher and optional date range.
- **Tool**: A data-source operation exposed to the Baseball Agent, such as querying hot data, querying cold data, retrieving leaderboard data, or resolving player names.

## Language

**AnalysisObjective**: The user's confirmed analytical question, independent of how far execution has progressed.

**ObjectiveState**: The current runtime projection of how far an AnalysisObjective is resolved. COMPLETE means its core original needs are met; LIMITED means useful bounded results remain after planning terminates.

**ArtifactRequirement**: A semantic information need supporting an Objective, expressed in the same language as an Artifact. It describes what is needed, not what currently exists.

**Initial Requirement**: An original requirement produced before planning; its identity, meaning and base criticality form the immutable business baseline.

**Supporting Requirement**: A Planner-added information need, optionally linked to a parent requirement. It cannot increase the original Objective's completion threshold.

**RequirementState**: The current satisfaction projection of an ArtifactRequirement, including references to the assessments supporting that state.

**ArtifactDescriptor**: The shared semantic description of needed or available information: type, canonical entities, data keys, time range, constraints, grain and population.

**Canonical Entity**: An entity identified by its identifier namespace, kind and stable identifier; display names alone do not establish identity.

**Constraint**: A typed restriction on information, with explicit user-confirmed or system-inferred origin. A source preference is not a hard constraint.

**Artifact**: An immutable produced result with identity, descriptor, payload reference, provenance and lineage. It has no absolute quality score.

**ArtifactAssessment**: Contextual usability of one Artifact for one Requirement and Objective, combining deterministic facts, a Judge result, a final grade, summary and limitations.

**AgentTask**: A semantic plan of work with requirement references and optional workflow dependencies; it contains no execution state.

**TaskExecution**: The runtime outcome of a task, distinct from each actual technical attempt.

**PlanningDecision**: A decision to PLAN, REPLAN or STOP_PLANNING. Terminal planning remains terminal until relevant external conditions change.

**CompletionReport**: The finalized internal record of accepted results and meaningful gaps for an analytical run.

**ResponsePackage**: The projection of accepted products, confirmed intent, relevant knowledge, provenance and limitations used to generate a response.

**Checkpoint**: A consistent recovery coordinate referencing independently persisted domain states and products.

**Qualification**: Eligibility for inclusion in a defined population or ranking.

**Analytical Population**: The explicit set of events a metric is computed over, defined by
its game types (regular season, postseason, Spring Training, exhibition) and its event
grain (fair batted-ball events, measured contact, or all qualifying pitches). It is a
typed constraint on the objective, requirement and artifact, never an implicit SQL
condition.

**Game Type**: The MLB StatsAPI competition code retained per Statcast row (R, F, D, L, W,
S, E, A) that makes the analytical population's game-type filter reproducible.

**Sample Adequacy**: Whether the available sample supports the particular conclusion sought; distinct from qualification.

**League Progress**: Authoritative season progress, independent of how much data a local archive has ingested.

**Provenance**: The recorded origin of an Artifact (source, source kind, reference, retrieval time).

**Lineage**: The `derived_from` references that connect a derived Artifact (for example a Feature Engine metric) back to its inputs.

**TaskAttempt**: One real technical try against a tool, with its own status, error code, retryable flag and optional artifact reference.

**Tool Capability**: What a tool can provide (source kind, supported artifact types, cost, availability), used by the Router before a task is executed. Capability is not a plan and not a requirement.

**Routing Decision**: The Router's selection of a tool for a task, with rationale, fallbacks and policy notes. The Router may override a Planner source preference but never a constraint or policy.

**Hard Failure**: A program-verifiable fact that disqualifies an Artifact for a Requirement (integrity, type, entity, required key, constraint or time-range mismatch). It cannot be overridden by the Judge.

**Soft Signal**: A program-detected limitation whose interpretation depends on the Requirement (zero rows, low sample, partial coverage, optional key missing). The Judge may reinterpret it.

**Evidence Purpose**: The requirement-level framing (EXISTENCE, DESCRIPTIVE, INFERENTIAL) that the Judge uses to interpret soft signals.

**Planner Terminal Latch**: The guard that prevents re-invoking a terminal Planner unless an external condition (new artifact, source, permission or user constraint) changes.

**Stop Reason**: The recorded reason planning ended for a run: COMPLETE, MAX_ROUNDS, BUDGET_EXHAUSTED, NO_RECOVERABLE_PATH, POLICY_BLOCKED or NO_PROGRESS.
