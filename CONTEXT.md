# Baseball Agent Domain Context

This document defines the shared vocabulary for the repository. Update it when a domain term is clarified or introduced; keep implementation details in code and durable architecture choices in ADRs.

## Purpose

Baseball Agent answers baseball analytics questions by selecting an appropriate data source, retrieving data, validating the result, computing derived features when needed, and formatting a user-facing response.

## Glossary

- **Analytics request**: A user's question about baseball performance, players, teams, seasons, or pitch events.
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

**Sample Adequacy**: Whether the available sample supports the particular conclusion sought; distinct from qualification.

**League Progress**: Authoritative season progress, independent of how much data a local archive has ingested.
