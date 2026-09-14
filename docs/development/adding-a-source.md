# Adding a source

A source is a **capability** plus a **tool adapter**. The Planner must not need changes to
use it.

## 1. Declare the capability

Add a `ToolCapability` so the Router can consider it:

```python
from app.agent.routing import ToolCapability

ToolCapability(tool="savant_api", source_kind="WEB",
               supported_artifact_types=("TABLE", "EVIDENCE"),
               cost="FREE", available=True, coverage="2024-present")
```

- `source_kind` participates in precedence (a user constraint or Source Mapping can
  restrict it).
- `cost` (`FREE`/`PAID`/`HIGH`) is filtered by `Router(permitted_costs=...)`. Paid/high-cost
  sources require user escalation (not yet wired to the CLI).
- `available` and `coverage` inform routing.

## 2. Write a tool adapter implementing the Tool Protocol

```python
from app.agent.executor import ToolResult


class SavantTool:
    name = "savant_api"

    def execute(self, task) -> ToolResult:
        # Return ToolResult(status="OK"|"EMPTY"|"ERROR", artifact=..., payload=...)
        ...
```

Rules:

- Return `OK` with an `Artifact` (immutable, with `Provenance` and any `lineage`), `EMPTY`
  for zero rows, `ERROR` for a technical failure.
- Set `retryable=True` only for a retryable technical error. `EMPTY` is a business
  outcome, never a retry.
- Redact errors through `app/tools/results.py:redact_secrets`; never leak a credential.
- For a database source, go through `app/tools/execution.py` so the SQL guard runs before
  the connection.

## 3. Register it

Add the tool to the `Executor` mapping and its capability to the `Router`.

## 4. Data-plane safety

If the source is a database, it is **read-only** and must go through the AST guard. If it
reads files, sandbox the paths. If it is the Web, return a raw result and extract
structured `EVIDENCE` artifacts separately (see the matrix: the Web evidence extractor is
not implemented yet).

## 5. Provenance and lineage

Every artifact must carry `Provenance(source, source_kind, reference, retrieved_at)`. A
derived artifact must carry `lineage=(input_artifact_id,)`.

## 6. Test

- Rejected/unsafe input never reaches the source (guard-before-connect).
- `EMPTY` is distinguished from `ERROR`.
- Errors are redacted.
- The Router selects the source under the right constraints and falls back otherwise.
- Add an end-to-end test in `tests/integration/` using a fake for the source.
