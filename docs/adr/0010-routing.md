# ADR 0010 — Data Routing vs Evidence Routing

Status: accepted

## Decision
Data routing answers only *where statistics are computed* (2015–2023 Parquet,
2024–2026 PostgreSQL). Evidence routing decides whether Dynamic Facts, Web, Knowledge or
local data is required, based on the requirement, freshness and source authority. The two
are orthogonal; a year is not an evidence router.
