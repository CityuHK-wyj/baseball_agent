# ADR 0021 — Answer Propositions Refine Requirements; Completion is Proposition-Coverage

Status: accepted (Phase 5B, 2026-09-25)

## Context

The holdout audit (F3) showed a Goal becoming `COMPLETE` because tools succeeded, artifacts
existed and the Judge emitted several claims — even while the final answer said the actual
question could not be answered. `RequirementState` was too coarse to express the answer
slots a question actually obliges (for example a trade investigation needs a verified trade
event, comparable before/after performance and a comparison), and the Judge marked an
`ANALYSIS` requirement `SATISFIED` as soon as *any* subject-relevant artifact existed.

The runtime already has a frozen `UserRequirement` graph and a coarse `RequirementState`
projection. A parallel proposition graph (with its own dependencies, scheduling and
planner integration) would duplicate both concepts and risk a second execution path, which
the architecture forbids. The mission is to keep one flow:
`User Message -> Semantic -> Goal/Requirements -> Planner -> Tool Execution -> Artifacts ->
Judge -> Answer`, and to strengthen `Artifacts -> Judge -> Answer`.

## Decision

Introduce `AnswerProposition` as a **derived refinement of `UserRequirement`**, never an
independent graph:

- Each proposition carries `requirement_id` (its parent) and `goal_id`. It is built once
  per Goal from the frozen requirements by `app/verification/propositions.py`
  (`build_propositions`). It is not a planner work item, has no capability, and cannot
  schedule or execute anything.
- Kinds: `IDENTITY`, `SCOPE`, `VALUE`, `FACT`, `EVENT`, `COMPARISON`, `EXPLANATION`,
  `GROUNDED`. A requirement may carry an explicit `propositions` list to expand one
  compound obligation into several slots; otherwise one requirement maps to one
  proposition. No example, player or query is hardcoded.
- Each proposition records subject/entity, time and population scope, required
  information/operation, mandatory flag, dependencies, and its coverage
  (`SATISFIED | PARTIAL | UNSUPPORTED | CONTRADICTED | NOT_ATTEMPTED | BLOCKED`), plus
  supporting/contradicting claim and artifact/evidence ids.

**Why existing contracts cannot carry this.** `UserRequirement` is the user-owned baseline
and must not be rewritten by the runtime; it has no coverage state and no
per-obligation evidence mapping. `RequirementState` is one value per requirement and cannot
express "the observation is supported but the causal explanation is not". `Claim` is a
final-answer statement, which is downstream of coverage. So a small derived
answer-obligation object with coverage is the minimal representation; it adds coverage
granularity without adding a second planning/execution graph.

## Completion rule

`GoalStatus` is computed **only** from proposition coverage:

- `COMPLETE` iff every mandatory proposition is `SATISFIED`.
- `LIMITED` when some mandatory proposition is supported/partial/contradicted, or when a
  non-high-risk explanation may be answered from labelled background.
- `FAILED` when nothing material is supported and background is not allowed.

A successful Artifact does not satisfy a proposition by itself; neither do multiple pages
that only mention the subject, nor the number of claims.

## Consequences

- `Artifacts -> Judge` is refined, not replaced: `RequirementState` still drives claims and
  limitations; proposition coverage is a total, one-way refinement of it plus
  proposition-specific evidence decisions.
- Explanatory questions can complete from narrative evidence without an unrelated local
  statistical artifact.
- An observed statistical change plus an interview yields a supported observation with a
  qualified explanation, not an automatic causal claim.
- The model is general across explanatory, statistical, historical, comparative and
  multi-source questions.
