# ADR 0029 — Fact vs Document Freshness and URL-Keyed Caching

Status: accepted (Web Research v2 architecture, 2026-09-27)

Architecture reference: `docs/architecture/WEB_RESEARCH_V2.md` §9, §6.1.
Implementation milestone: M3. M1 establishes only the body-version metadata and the cache
*contract* (ADR 0027 §body-version); **no cache behavior is active in M1**.

## Context

Phase 5B has no retrieval cache and no freshness model. It guesses `publication_date` from a
few regexes, discards RSS `pubDate`, has no "published within N days" filter and no `as_of`.
Repeated same-subject research refetches everything. A cache keyed on a content hash would be
unusable (the hash is unknown before fetching) and would conflate two different documents
that happen to share bytes.

There is also a conceptual conflation: how recent a *fact* must be is not the same as how
recent a *document* is. An old document can still assert a current fact; a new document can
be irrelevant.

## Decision

### Two independent axes

- **Fact freshness** — how current the *state of the world* must be, derived from the Goal:
  `VOLATILE` (current injury/roster/trade/standings, current-year `VALUE`), `IMMUTABLE`
  (past-season statistics, historical events, encyclopedia facts), or `MIXED`.
- **Document freshness** — when the *document* was published and last retrieved, from
  `publication_date` and `retrieved_at`. Measured, never inferred from fact freshness.

A cache entry is usable only when both hold: its fact class tolerates the age, and for
`VOLATILE` it has been revalidated within TTL and carries a valid `as_of`.

| Fact class | Document age signals | Cache policy |
|---|---|---|
| `IMMUTABLE` | `publication_date`, `retrieved_at` | TTL 7–30 days; may serve without revalidation |
| `VOLATILE` | must be revalidated now | TTL 5–15 min; conditional request; never serve stale without `as_of` |
| `MIXED` | per-URL | per-URL class |

### URL-keyed cache, content hash as version token

- **Lookup key = canonical URL** (plus `extractor_version`). The body hash is unknown before
  fetching, and different URLs may share content; the cache is URL-keyed
  (`cache[url] -> versioned entry`).
- **Content hash is a version/validation token, not the lookup key.** `raw_content_hash`
  identifies the server version; `body_hash` identifies the extraction version. Conditional
  revalidation (`ETag`/`If-None-Match`, `Last-Modified`/`If-Modified-Since`, or raw-hash
  comparison on a 200) decides whether the cached version is current.
- A URL may hold several historical versions; only the newest is served. Two URLs with equal
  bytes remain two entries; dedup by hash happens later at the artifact level.

### Time fields are never rewritten by a cache hit

- `retrieved_at` — when the body was actually fetched from the network; preserved across hits.
- `revalidated_at` — when a conditional request confirmed the body is current (metadata only).
- `as_of` — the point in time the fact is asserted to hold (`VOLATILE`: revalidation/fetch
  time; historical: publication date or an explicit dataset as-of).

A cache hit records `cache=hit|revalidate|miss` and never rewrites `retrieved_at` or `as_of`.

### Storage

In-process LRU plus an optional disk store under `.runtime/web_cache/` with `0700`/`0600`
permissions, Git-ignored, no request headers or credentials, bodies out of diagnostics. Cache
entries store `extractor` + `extractor_version`; a version mismatch invalidates spans and
forces re-extraction. The cache respects the SSRF guard.

## Invariants

- A `VOLATILE` fact is never served from a stale entry without revalidation and a valid
  `as_of`.
- A cache hit preserves the original `retrieved_at` and `as_of`.
- Offsets are valid only for the cached `(body_hash, extractor_version)`.
- No credentials are stored; diagnostics policy (ADR 0024/0025) is unchanged.

## Consequences

- Historical questions reuse immutable documents cheaply; current questions stay honest.
- Repeat-question latency and cost drop measurably in the benchmark.
- Freshness is a first-class, testable property rather than a regex guess.

## Non-goals

- No semantic embedding cache.
- No cross-Goal shared cache beyond the documented store semantics.
- No cache behavior in M1; M1 only defines the contract.
