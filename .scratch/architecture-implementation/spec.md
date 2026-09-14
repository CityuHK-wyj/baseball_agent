# Frozen architecture implementation

Status: IN_PROGRESS

Source: user development request; blog content/knowledge/decisions.md D034–D066 as of 2026-09-14. Public testing seams below are selected under the user's explicit authorization to design the testing strategy autonomously.

## Milestone 1: safe domain foundation

1. Settings read environment at construction time and never reveal credentials in repr. No credential defaults exist in source or example configuration.
2. Secret scanning produces only file/line/rule plus SECRET_REDACTED. Test synthetic provider keys, URL passwords, literal keyword credentials and staged content. Existing history is audited separately and never silently published.
3. AnalysisObjective and ArtifactRequirement are frozen definitions; ObjectiveState and RequirementState are distinct initial projections. ArtifactDescriptor shares canonical entity, semantic data keys, date range and typed constraints between requirements and artifacts.
4. The public requirement catalog preserves initial requirements. Additions must have PLANNER_ADDED origin, unique identities, a known objective and valid same-objective parent. Replaying the same addition is idempotent; identity reuse with changed content fails. Initial criticality cannot change.
5. Legacy unsafe SQL/agent entry points fail closed until a tested read-only executor exists. No analytics writes are executed during development. This temporary restriction must be explicit in documentation.

## Later milestones

Matcher + contextual assessment (hard failure veto; soft facts; Judge summary); RequirementState and ObjectiveState transitions; semantic Planner PLAN/REPLAN/STOP_PLANNING with terminal latch; Router policy and fallback; deterministic Executor retry; accepted-products ResponsePackage; Shared Context projection; independent Operational PostgreSQL and filesystem payload storage; checkpoint consistency and idempotent resume.

Integration/adversarial cases: wrong entity/schema, corrupt artifact, hard time mismatch, 0 rows, partial coverage, low sample, contextual Judge adjustment, supporting gaps, source failure, policy/budget/max rounds, terminal reinvocation, duplicates, checkpoint interruption, malformed tools, SQL mutation/function/file escape, secret output. Each later ticket must specify its seam and red/green evidence before implementation.

## Milestone 2: evaluation, planning and accepted-product slice (delivered)

Definitions and states (ADR 0001/0005):

- `AnalysisObjective` and `ArtifactRequirement` are frozen definitions; `ObjectiveState`
  and `RequirementState` are separate projections created with the definitions.
- `ArtifactDescriptor` carries artifact type, canonical entities, required and optional
  data keys, required time range, typed constraints, granularity and population scope.
- `ArtifactRequirement` adds `evidence_purpose` and `min_row_count` as defaulted,
  requirement-level context. Initial Requirements stay immutable; Planner additions use
  `origin=PLANNER_ADDED` with a same-objective parent.

Artifacts and assessment (ADR 0002):

- `Artifact` is immutable: id, descriptor, payload reference, provenance, lineage, row
  count, observed time range, integrity. No quality field.
- `validate_artifact` produces hard failures and soft signals.
- `RuleBasedJudge` interprets soft signals against `evidence_purpose`.
- `ArtifactAssessment` binds artifact + requirement (+ objective) and stores the
  deterministic result, judge result, final level, short summary, usable-for and
  limitations. Hard failures force `REJECT` at contract level and in the service.
- `ArtifactRegistry` stores artifacts and lineage and never judges quality.

Planning, routing and execution (ADR 0003):

- `AgentTask` carries no execution state; `TaskExecution` and `TaskAttempt` carry
  runtime outcome and per-try history.
- `PlanningDecision` is PLAN/REPLAN/STOP_PLANNING and enforces the terminal contract.
- `RuleBasedPlanner` decides from requirement states, artifact index, assessment
  summaries, round, budget and recoverable/policy-blocked gaps. It never edits an
  Initial Requirement.
- `PlannerTerminalLatch` blocks repeated terminal invocation until the external
  condition fingerprint changes.
- `Router.route` applies System Policy > User Hard Constraint > Source Capability >
  Planner Preference > Router Optimization.
- `Executor` performs bounded retries only for `retryable` tool errors; `EMPTY` is a
  business outcome, not a retry.

Finalization:

- `CompletionReport` summarizes objectives, per-requirement completion, accepted
  artifacts, limitations, gaps, plan revisions, execution summary and stop reason.
- `ResponsePackage` is projected from accepted assessments only. It has no field for
  attempts, rejected evidence, plans or routing history.

## State transitions

Requirement: `PENDING` (no assessment) -> `SATISFIED` (any STRONG/ACCEPTABLE) |
`PARTIAL` (only WEAK) | `UNSATISFIED` (only REJECT). Unchanged evidence returns the
previous projection so version bumps are meaningful.

Objective (core Initial Requirements only): all core `SATISFIED` -> `COMPLETE`;
otherwise while planning is open -> `PENDING`/`IN_PROGRESS`; once planning is terminal
-> `LIMITED` if any core is SATISFIED/PARTIAL, else `FAILED`. Optional gaps are retained
even when COMPLETE.

Planning: `PLAN` -> `REPLAN`* -> `STOP_PLANNING`. Terminal latches on COMPLETE,
MAX_ROUNDS, BUDGET_EXHAUSTED, NO_RECOVERABLE_PATH, POLICY_BLOCKED or NO_PROGRESS.

## SQL security

`guard_read_only_sql` is the structural read-only control (ADR 0004). It parses with
sqlglot and rejects multiple statements, non-SELECT roots, DDL/DML, administration
statements, forbidden functions, non-allowlisted tables (schema-qualified identity)
and file paths outside an explicit root.

Milestone 3 wires it into execution (ADR 0006). `app/tools/execution.py` validates
before opening any connection, runs PostgreSQL in a read-only transaction with a
statement timeout, wraps LIMIT-less reads and bounds rows, and redacts every error
summary through `app/tools/results.py:redact_secrets`. `main.py` and the plotting
helper no longer expose unguarded analytical paths.

## Tests and evidence

`python3 -m unittest discover -s tests -v` runs the suite. Milestone 1 evidence: 15
tests. Milestone 2 adds artifact/validation/judge/registry, state derivation, planner
and latch, routing precedence, executor retry/empty, orchestrator end-to-end and the
SQL AST guard. Milestone 3 adds guarded read-only execution and the redacted tool
result contract. Total: 81 tests.
