# Baseball Agent

Baseball analytics agent project, currently **IN_PROGRESS**. The architecture is frozen; the first tested slice introduces immutable Objective/Requirement definitions and their separate initial state projections.

## Target architecture

Interaction → Normalization → Planning & Orchestration → Data & Tool → Evaluation & Sufficiency → Response. Shared Context, Runtime State and Governance span the flow; these are responsibilities, not additional deployed services.

The old unbounded LLM loop, unvalidated SQL adapters and analytics-writing loaders are disabled. This is deliberate containment, not a finished query engine. The next ticket restores SQL reads behind AST validation and a database/file sandbox. Do not run the historical commits: they contain exposed credentials and unsafe execution paths.

Run `python3 -m unittest discover -s tests -v` and `python3 scripts/secret_scan.py`. Configure local credentials through environment variables using `.env.example`; Python does not automatically load `.env`. Optional pre-commit setup: install pre-commit in your development environment, then run `pre-commit install`.

The [development status](docs/development-status.md) records implementation evidence, known gaps, security actions and the exact continuation plan. Specifications and tickets live in `.scratch/architecture-implementation/`.
