# Observability and evaluation as structured, redacted data

Status: accepted

## Context

The brief (§58, §59) requires structured observability fields and system-level evaluation
metrics, with a hard rule that no credential may ever be recorded. Nothing existed.

## Decision

- `app/observability/metrics.py`: `RunEvent` (run_id, event_type, subject_ref, agent,
  status, duration_ms, retry_count, replan_count, source, tool, tokens, cost_usd, message)
  and `RunMetrics.record(...)`. Every free-text field is passed through
  `redact_secrets` before storage, so a credential cannot reach a sink. This is
  bookkeeping, not an agent.
- `app/observability/evaluation.py`: `RunSummary` (derivable from a `RunResult` via
  `from_result`) and `RunEvaluation` with completion/replan/retry/source-failure rates,
  average steps and average accepted artifacts. `judge_disagreement_rate` is computed
  from supplied deterministic/judge level maps.
- Rates are deliberately simple integer ratios, not weighted precision.

## Alternatives

- Log raw messages and rely on a downstream filter: rejected; redaction must happen at
  the recording boundary.
- A metrics dashboard or time-series backend now: rejected; structured numbers first.

## Consequences

- Metrics and evaluation are deterministic and testable without a live run.
- The Orchestrator does not yet emit `RunEvent`s or collect `RunSummary`s in its loop;
  that wiring is a follow-up.
- `RunMetrics` holds events in memory; a sink is a future `Protocol`.
