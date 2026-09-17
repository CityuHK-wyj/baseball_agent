# Planner terminal latch and an orchestrator-managed loop

Status: accepted

## Context

D061 gives the Planner ownership of PLAN / REPLAN / STOP_PLANNING. D065 requires
that once the Planner returns a terminal decision, the Orchestrator must not invoke
it again unless a meaningful external condition changes. Without an explicit
mechanism the loop can cycle forever (`Planner -> no plan -> Orchestrator -> Planner`),
which the user's brief calls out as a required adversarial case.

## Decision

- `PlanningDecision` is a contract with `kind`, `tasks`, `planner_terminal` and
  `terminal_reason`. A validator enforces: `STOP_PLANNING <=> planner_terminal`,
  terminal decisions carry no tasks, and terminal decisions require a reason.
- `PlannerTerminalLatch` stores the terminal reason plus an opaque external-condition
  fingerprint. It refuses re-invocation for the same fingerprint and clears only when
  the fingerprint changes. Internal counters (rounds, assessments) are deliberately
  not part of the fingerprint.
- The Orchestrator computes the fingerprint from externally meaningful state:
  the set of registered artifact ids and the permitted source set. New artifacts, new
  sources or changed permissions reopen planning; a bare round increment does not.
- The Orchestrator additionally latches `NO_PROGRESS` when a full round registers no
  new artifact id, and the Planner stops on `MAX_ROUNDS` and `BUDGET_EXHAUSTED`.
  `POLICY_BLOCKED` is distinguished from `NO_RECOVERABLE_PATH` using
  `Router.candidate_sources` versus `Router.eligible_sources`.
- `RuleBasedPlanner` raises if invoked with `planner_terminal=True`; the Orchestrator
  never does so because the latch is checked first.

## Alternatives

- Let the Orchestrator decide when to replan (old D042): superseded by D061.
- Track only a boolean terminal flag: rejected; it cannot distinguish "terminal, no
  change" from "terminal, but a new source just appeared".
- Rely on a global round cap alone: rejected; it burns budget and hides the real
  stopping reason.

## Consequences

- Stopping is explainable: `CompletionReport.stop_reason` records COMPLETE, MAX_ROUNDS,
  BUDGET_EXHAUSTED, NO_RECOVERABLE_PATH, POLICY_BLOCKED or NO_PROGRESS.
- Tests cover terminal re-invocation, no-progress termination and budget/round stops.
- The fingerprint definition is a deliberate policy surface; adding new
  externally-meaningful state (permissions, user constraints) means extending
  `Orchestrator._external_condition`.
