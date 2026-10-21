# ADR 0020 — Analytical Enumeration Completeness Contract

Status: accepted (Phase 5A, 2026-09-21)

## Context

Analytical artifacts carried rows and scope but no statement of *whether the row set was the
complete qualified population or a truncated top-N*. The runtime therefore consumed a
per-side `LIMIT 100` as if it were the full population, joined two such lists and declared a
global maximum/difference (holdout F5). A player outside both top-100 lists could hold the
true extreme.

## Decision

Every `DATABASE_RESULT` and `DERIVED_MEASURE` carries an `EnumerationContract`:

- `population`, `season`, `date_range`, `source` — the source scope;
- `qualification` — the applied qualification (e.g. `PA >= 100`);
- `ordering` — the applied sort;
- `limit` and `returned_rows` — the applied limit and what came back;
- `completeness` — `COMPLETE` / `TRUNCATED` / `UNKNOWN`;
- `identity_columns` — stable columns that identify a row.

Completeness is `COMPLETE` for a scalar result, for a grouped result with no limit, and for a
grouped result that returned fewer rows than its limit; it is `TRUNCATED` when a grouped
result filled its limit.

Two enforcement points:

1. **Comparison inputs are expanded.** A `local_analytics` Need that feeds a
   `python_compute` Need is compiled with a bounded complete scan
   (`for_comparison=True`, ceiling `5000`) instead of the LLM's intermediate `LIMIT`. If the
   ceiling is reached the contract records `TRUNCATED`. Safety budgets are kept; only the
   intermediate truncation is removed.
2. **Global operations require completeness.** `python_compute` refuses a
   `global_comparison` (rank/difference/extremum) over any input whose contract is not
   `COMPLETE` and returns a typed `INCOMPLETE_ENUMERATION` block. The derived artifact
   inherits the worst input completeness and records each input's scope in `source_scopes`
   (season, population, date range, measures, qualification, completeness, source).

A single sorted SQL query with `ORDER BY ... LIMIT n` is still a legitimate global top-N
because the database sorts the whole qualified population before limiting; only an
*intermediate* truncation followed by a new global computation is unsafe.

## Invariant

A global ranking cannot derive from truncated intermediate rankings unless recorded proof
shows the truncation preserves the result.

## Consequences

- Global maxima/differences are computed over complete qualified populations, or refused.
- Cross-source results retain each input's time, population, qualification and completeness.
- Limits and safety ceilings remain; they are now explicit rather than silent.

## Phase 5A closure addendum (independent revalidation)

Individual input completeness was insufficient. Computation is now operation-aware:

1. **Structured operation.** `GlobalOperation(kind, identity, partition_by, dedup_key)`
   declares the semantics: `LOCAL`, `EXTREMUM`, `RANK`, `DELTA`, `JOIN`, `UNION` or
   `UNKNOWN`. The runtime infers it from explicit planner constraints (preferred), the
   objective/goal wording, and the actual input enumerations. Anything unclassifiable with
   more than one input fails closed as `UNKNOWN`.
2. **Compatibility rules** (`app/verification/enumeration.py`): every contributing input
   must be `COMPLETE`; shared identity domain, population and qualification are required;
   `RANK`/`UNION`/`EXTREMUM` require disjoint partitions (or an explicit dedup proof);
   `DELTA` requires a proven per-entity identity across distinct partitions; `JOIN` aligns
   identities within one population. Same-source/same-window inputs are rejected as
   `SOURCE_OVERLAP`. Different seasons and different sources are *not* mismatches — they are
   judged against the declared operation.
3. **Measured completeness at the budget.** When a grouped scan fills the 5,000-row budget,
   the runtime issues a bounded `COUNT(*)` pushdown over the same query to measure the exact
   group count. It marks `COMPLETE` only when the count proves it; otherwise `TRUNCATED`
   with the measured count. Completeness is no longer merely inferred, and safety limits are
   retained.
