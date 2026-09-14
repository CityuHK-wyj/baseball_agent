# Planning, routing and accepted-product finalization

Status: OPEN
Blocked by: 02-verified-execution.md, 03-assessment-feedback.md

Implement PLAN/REPLAN/STOP_PLANNING and a terminal latch keyed to meaningful external conditions. Add supporting requirements through the catalog; route according to capability/coverage/policy with preferences as soft choices. Executor owns bounded technical retries. Orchestrator coordinates states, budgets and scheduling and emits CompletionReport/ResponsePackage from accepted products only. Test initial/revised planning, source fallback, policy/budget/max rounds, malformed reports and rejected/history exclusion.
