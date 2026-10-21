# ADR 0024 — Full-Fidelity Diagnostics and Latency Observability

Status: accepted (Phase 5B, 2026-09-25; amended by ADR 0025, 2026-09-27)

> **Closure amendment (ADR 0025).** Redaction is field-aware *and* value-aware: credential
> fields are masked, token-usage telemetry (`subject_tokens`, `required_tokens`,
> `matched_tokens`, usage counts) is preserved, and credentials embedded in URLs,
> `Authorization` headers, bearer tokens and cookies are scrubbed. The diagnostics
> directory/file are created `0700`/`0600`. `composer_recompose` is replaced by
> `consistency`, and LLM call counts (`llm_call_stats`) are recorded.

## Context

The previous verbose traces exposed only counts and summarized artifact metadata
(`4 relevant grounded source(s)`), truncated data at 400 characters, and omitted the actual
URLs, passages and verification decisions. It was impossible to audit or reproduce a
research result from a trace. The console trace must stay compact, but the only copy of the
evidence must not be truncated to fit it.

## Decision

Add `app/observe/diagnostics.py`:

- `LatencyTracker` records per-stage millisecond spans, including `goal_end_to_end`.
- `DiagnosticRecorder` writes a redacted, bounded JSON record per Goal to
  `<runtime_dir>/diagnostics/<session>__<goal>.json`.

For a research Goal the record can reconstruct: the search objective and queries, selected
provider, candidate hits, actually fetched documents (URL, title, publication date,
retrieval time, status, content hash, bounded excerpt), retrieval failures, evidence spans
with offsets, per-proposition evidence findings (support/contradiction decisions and
rationale), proposition coverage, the Judge decision and consistency result, the claim ->
citation mapping, and the latency summary.

Latency stages: `conversation`, `semantic`, `requirements`, `planning_and_tools`,
`tool:<capability>`, `web:search`, `web:fetch`, `web:retrieve`, `web:verify`, `judge`,
`composer`, `consistency`, `goal_end_to_end`.

Storage limits and redaction: text is capped (`max_text`, 4000 by default), collections are
capped. Credential fields (`api_key`, `access_token`, `authorization`, `cookie`, `password`,
`secret`, credential suffixes) are masked, while analysis telemetry such as
`subject_tokens`, `required_tokens` and `matched_tokens` is preserved; credentials embedded
in arbitrary strings (`?token=...`, `Authorization: Bearer ...`, cookies) are scrubbed. The
diagnostics directory/file are created `0700`/`0600`. `.runtime/` and `.scratch/` are
ignored by Git, so private traces, credentials and user-provided holdout records are never
committed. Diagnostics never alter Planner, Tool, Judge or response semantics, and a
diagnostics failure never breaks a turn.

## Consequences

- Codex (or any auditor) can inspect the real URLs, passages and decisions behind a
  research result.
- This stage measures; it does not optimize. It enables later performance work.
