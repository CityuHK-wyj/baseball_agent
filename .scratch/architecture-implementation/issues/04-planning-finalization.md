# Planning, routing and accepted-product finalization

Status: DONE
Blocked by: 03-assessment-feedback.md (02-verified-execution.md remains PARTIAL for live sources)

Implement PLAN/REPLAN/STOP_PLANNING and a terminal latch keyed to meaningful external conditions. Add supporting requirements through the catalog; route according to capability/coverage/policy with preferences as soft choices. Executor owns bounded technical retries. Orchestrator coordinates states, budgets and scheduling and emits CompletionReport/ResponsePackage from accepted products only. Test initial/revised planning, source fallback, policy/budget/max rounds, malformed reports and rejected/history exclusion.

## Comments

- Delivered in ADR 0003: `app/models/planning.py`, `app/agent/planner.py`,
  `app/agent/routing.py`, `app/agent/executor.py`, `app/agent/orchestrator.py`,
  `app/agent/response.py`.
- Tests: `tests/test_planner.py`, `tests/test_routing.py`, `tests/test_executor.py`,
  `tests/test_orchestrator.py` including terminal reinvocation, no-progress stop,
  budget/max-round/policy stops and rejected-history exclusion from ResponsePackage.
- Live source execution awaits ticket 02; the loop is proven with deterministic tools.
