# ADR 0030 — Official Structured Sources and the Retrieval-vs-Computation Boundary

Status: accepted (Web Research v2 architecture, 2026-09-27)

Architecture reference: `docs/architecture/WEB_RESEARCH_V2.md` §3.5, §6.1, §6.2.
Implementation milestone: contract in M1; optional structured adapter family in M4
(config-gated). No computation moves into Web Research at any milestone.

## Context

The Phase 5B baseline has no structured-source interoperability for research: `dynamic_fact`
handles only `currentTeam` (repaired), and schedule/season-stats/standings/transactions/
injuries, Retrosheet/Lahman history and Savant CSVs are unreachable. Meanwhile an official
API response is neither a news article nor a raw source table, and it must not be presented as
either. There is also a delivery risk: adding structured sources can drift into re-implementing
the analytics engine inside research, or into forcing all computation into Python.

## Decision

### Structured sources are record retrievers

Official structured sources (starting with MLB StatsAPI where reachable) are optional source
adapters **inside** `web_research`, probed at runtime and never assumed available. A structured
adapter may **fetch and render** a record; it may not aggregate, group, rank, derive measures
or compare entities.

### Retrieval-vs-computation boundary

- `local_analytics` owns aggregation over raw PostgreSQL/Parquet source data (Safe IR,
  measure verification, `EnumerationContract`). Raw-data aggregation does **not** move to
  `python_compute` and does **not** move to `web_research`.
- `python_compute` owns bounded temporary computation over **already-accepted Artifacts**
  (combine/join/difference/transform) under the Phase 5A `GlobalOperation`/enumeration gates.
  It is the exception for cross-artifact work, not the default engine.
- `web_research` computes nothing. If an obligation needs aggregation, the Planner uses
  `local_analytics`; if it needs bounded arithmetic over accepted artifacts, the Planner uses
  `python_compute`. `web_research` fails closed (`UNSUPPORTED_OPERATION` /
  `INSUFFICIENT_ENUMERATION`) rather than computing.

### Structured record provenance

Every structured record carries a `StructuredRecord` envelope: `record_id` (deterministic
source + endpoint + key fields), `source_id`, `source_url`, `as_of`, `conversion_version`,
`raw_fields` (verbatim provider fields). The normalized `body_text` begins with a
deterministic provenance header naming those fields and is explicitly a **labeled rendering**,
not a news article and not the unconverted official original. `conversion_version` changes
produce a new body version (`body_hash`, ADR 0027). `RetrievedDocument.source_quality` is
`official_structured_record:<source_id>`, `EvidenceSpan.location` is `structured:<record_id>`,
and the raw record plus envelope is stored in the artifact under `structured_records`.

Admissibility is unchanged: the rendered body is a fetched `DOCUMENT_BODY` with
`retrieval_status=OK` and exact offsets, so `verify_span` (ADR 0022/0025) still decides
support. The provenance fields add no new evidence authority; they only prevent the rendering
from being presented as journalism or as an unconverted official original.

## Invariants

- No structured adapter aggregates or derives measures.
- Raw-data aggregation stays in `local_analytics`; Python is not the universal compute path.
- A structured record preserves raw fields, record identity, source, `as_of` and conversion
  version.
- Normalized structured text never impersonates a news body or the unconverted original.
- A source that is unreachable fails typed and gracefully; availability is never assumed.

## Consequences

- Authoritative records become evidence without weakening the evidence contract.
- The analytics responsibility stays where the accepted contracts put it.
- Structured breadth is optional and measured (M4), not assumed.

## Non-goals

- No new Planner-visible capability.
- No aggregation/derived computation inside `web_research`.
- No modification of the Judge, Evidence Verifier, Composer or the enumeration contract.
