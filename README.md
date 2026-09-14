# Baseball Agent

Baseball analytics agent project, currently **IN_PROGRESS**. The architecture is frozen. The first tested vertical slice is now in place: immutable Objective/Requirement definitions with separate state projections, contextual Artifact assessment with a non-overridable hard-failure gate, a PLAN/REPLAN/STOP_PLANNING Planner with a terminal latch, capability-based routing, bounded execution retries, and a ResponsePackage built from accepted products only.

Live data access is guarded and wired but not yet exercised against a real database. The old unbounded LLM loop and analytics-writing loaders fail closed. All analytical SQL now runs through a read-only AST guard that validates *before* connecting, enforces read-only transactions, bounds rows and redacts errors (`app/tools/execution.py`). The raw DuckDB read in `main.py` was removed. Do not run the historical commits on the old branch: they contain exposed credentials and unsafe execution paths.

Run `python3 -m unittest discover -s tests -v` and `python3 scripts/secret_scan.py`. Configure local credentials through environment variables using `.env.example`; Python does not automatically load `.env`. Optional pre-commit setup: install pre-commit in your development environment, then run `pre-commit install`.

The [development status](docs/development-status.md) records implementation evidence, known gaps, security actions and the exact continuation plan. Specifications, tickets and ADRs live in `.scratch/architecture-implementation/` and `docs/adr/`. The next agent should start with [docs/codex-handoff.md](docs/codex-handoff.md).
