# Baseball Agent

Baseball analytics agent project, currently **IN_PROGRESS**. The architecture is frozen. The first tested vertical slice is now in place: immutable Objective/Requirement definitions with separate state projections, contextual Artifact assessment with a non-overridable hard-failure gate, a PLAN/REPLAN/STOP_PLANNING Planner with a terminal latch, capability-based routing, bounded execution retries, and a ResponsePackage built from accepted products only.

Live data access is still disabled. The old unbounded LLM loop, unvalidated SQL adapters and analytics-writing loaders fail closed. A read-only SQL AST guard (`app/validation/sql_guard.py`) is implemented and tested, but it is not yet wired to a database or DuckDB connection; that is ticket `02-verified-execution.md`. Do not run the historical commits: they contain exposed credentials and unsafe execution paths.

Run `python3 -m unittest discover -s tests -v` and `python3 scripts/secret_scan.py`. Configure local credentials through environment variables using `.env.example`; Python does not automatically load `.env`. Optional pre-commit setup: install pre-commit in your development environment, then run `pre-commit install`.

The [development status](docs/development-status.md) records implementation evidence, known gaps, security actions and the exact continuation plan. Specifications, tickets and ADRs live in `.scratch/architecture-implementation/` and `docs/adr/`. The next agent should start with [docs/codex-handoff.md](docs/codex-handoff.md).
