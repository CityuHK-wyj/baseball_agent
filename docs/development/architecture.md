# Architecture (developer guide)

The macro architecture is frozen. This guide explains where each responsibility lives.
Read [../../CONTEXT.md](../../CONTEXT.md) for vocabulary and `docs/adr/` for the rationale
behind each decision.

## Layers

```text
Interaction      -> app/conversation (placeholder), app/cli.py
Normalization    -> app/semantic/*
Planning & Orch. -> app/agent/{planner,router,source_mapping,orchestrator,review}.py
Data & Tool      -> app/tools/*, app/features/*
Evaluation       -> app/assessment/*, app/state/services.py
Response         -> app/agent/response.py, app/llm/response.py
```

Cross-cutting: `app/context` (Shared Knowledge & Context), `app/persistence`,
`app/observability`, `app/validation`.

## Definition vs runtime state

```text
AnalysisObjective  ── ObjectiveState
ArtifactRequirement ── RequirementState
AgentTask → TaskExecution → TaskAttempt → Artifact → ArtifactAssessment
```

Definitions say *what it is*; state says *how it is going*. `ObjectiveState` and
`RequirementState` are created with their definitions and updated by pure services in
`app/state/services.py` — not by agents.

## Who decides what

| Component | Decides | Must not |
| --- | --- | --- |
| Semantic normalization | what the user means | choose sources |
| Requirement Decomposer | what information is needed | choose tools |
| Planner | what to do next (PLAN/REPLAN/STOP) | re-interpret intent, edit Initial Requirements |
| Router | where/how to execute a task | judge evidence, decompose requirements |
| Executor | bounded technical retries | semantic recovery |
| Judge | contextual usability of an artifact | override a hard failure |
| Orchestrator | scheduling, budgets, finalization | domain reasoning |

Precedence everywhere: **System Policy > User constraint > Capability/Source Mapping >
Planner preference > Optimization.**

## Artifact lifecycle

1. A tool or the Feature Engine produces an immutable `Artifact` (descriptor, payload
   reference, provenance, lineage).
2. `validate_artifact` produces a `DeterministicResult` (hard failures + soft signals).
3. The Judge interprets soft signals for the requirement → `JudgeResult`.
4. `ArtifactAssessment` binds artifact + requirement (+ objective) with a final level and a
   short summary. A hard failure forces `REJECT`.
5. State services update `RequirementState` → `ObjectiveState`.

## Context boundary

`ContextService` is a capability, not an agent. It projects bounded items by purpose:
`PLANNER` gets knowledge and summaries; `RESPONSE` gets accepted products only. Attempts,
routing decisions, drafts, judge reasoning, rejected evidence and unused RAG are never
projected. Run-scoped items are isolated by `scope_run`/`run_id`.

## Persistence and checkpoints

`RunRecorder` writes payload → artifact metadata → assessment → states → execution
references → checkpoint, in that order, so no state ever references an artifact that
failed to persist. A `Checkpoint` is a recovery coordinate: state-version refs plus work
refs, not a serialized `AgentState`. `ResumeService` reclassifies interrupted executions
and reuses artifacts.

## Add a new module

1. Write the contract in `app/models/` (frozen, `extra="forbid"`).
2. Implement the behavior behind a Protocol so a deterministic version is testable.
3. Add tests under `tests/`.
4. If it is a durable decision, add an ADR in `docs/adr/`.
5. Update `docs/blog-implementation-matrix.md`, `docs/development-status.md` and
   `docs/codex-handoff.md`.

See [adding-a-metric.md](adding-a-metric.md) and [adding-a-source.md](adding-a-source.md).
