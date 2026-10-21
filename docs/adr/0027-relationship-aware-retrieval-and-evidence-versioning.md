# ADR 0027 — Relationship-Aware Retrieval and Evidence Versioning

Status: accepted (Web Research v2 architecture, 2026-09-27)

Architecture reference: `docs/architecture/WEB_RESEARCH_V2.md` §5, §6, §6.1.
Implementation milestones: query facets, ranking and parallel fetch in M2; the body-version
contract in M1; the `trust_weight` ranking benefit is measured in M2.

## Context

Phase 5B retrieval ranks candidates by subject-token overlap and fetches the first
`max_fetches` hits in backend-interleave order. A page whose *title* mentions the subject
can consume the fetch budget before a page that actually establishes the requested
relationship is reached. Search is fully sequential. Span selection takes the first six
token-sharing sentences, so a long article's relevant passage can be missed. There is also a
latent authority bug: the descriptor's integer tier (`1=official … 4=open web`) entered an
additive bonus, so lower-authority sources would score higher.

## Decision

### Query planning

`ResearchQueryPlan` (internal) is built deterministically with facet-tagged variants:
`ENTITY`, `METRIC`, `EVENT`, `INTERVIEW`, `FRESHNESS`, `MISSING`. Aliases come from
`entity_references` and the entity directory; historical dates come from the structured
`time_scope`; mandatory propositions with no facet are recorded in `facets_missing`. At most
one bounded LLM refinement call may add phrasings; it preserves the subject prefix and is
skipped when no LLM is configured or the budget forbids it.

### Candidate ranking

`CandidateRanker.score(hit, variant, proposition, source)` is deterministic and lexical (no
embeddings): subject/alias coverage, required-information (metric/event) coverage, scope
coverage, intent match, **`trust_weight`**, freshness match, minus discovery-only and
duplicate-domain penalties.

`trust_weight` is a float where **a larger value means a more trusted source**
(`1.00` official, `0.85` encyclopedia/reference, `0.55` publisher news, `0.25` open web,
`0.00` discovery-only). It replaces the ambiguous `trust_tier` integer. A regression test
asserts an official source outranks an open-web source for an identical lexical match.

Ranking is order-stable (ties broken by URL) so concurrent retrieval is reproducible.

### Fetch and extraction

A bounded ladder: cache lookup → primary fetch (SSRF guard) → bounded transient retry with
jittered backoff → extraction ladder (BeautifulSoup → main-content → JSON-LD/OpenGraph →
JSON/XML) → alternate URL for the same logical document → typed terminal failure → provider
fallback. A provider outage is never silently reported as `NO_RESULTS`. `MIN_PAGE_TEXT`, the
SSRF policy and the `body_text` contract are unchanged.

### Body-version and offset contract

Three versioned artifacts:

1. **Raw response** — `raw_bytes`, `raw_content_hash = SHA-256(raw_bytes)` (stored on the
   existing `RetrievedDocument.content_hash`), `final_url`, `content_type`, `encoding`.
2. **Extracted document body** — `body_text = extract(raw_bytes, extractor,
   extractor_version)`, deterministic and replayable, with `body_hash = SHA-256(body_text)`.
   Changing the extractor or its normalization rules is a new body version.
3. **Evidence span** — `span.text`, `start`, `end` are character offsets into the *same*
   `body_text`, enforcing `body_text[start:end] == span.text` and
   `span_hash = SHA-256(span.text)`. Offsets are never raw-byte offsets.

A persisted span is valid only against the `(document_id, body_hash, extractor_version)` it
was created from. Cache/replay reuse must match those; a mismatch re-extracts rather than
reusing stale offsets. `body_hash` and `extractor_version` are additive metadata; no evidence
contract changes.

## Invariants

- The requested factual relationship, not mere subject presence, drives ranking, fetching and
  passage selection.
- Higher `trust_weight` can never be outranked by a lower one under identical lexical match.
- Concurrency cannot change the selected set or the resulting coverage (order-stable merge).
- Every admitted span satisfies the offset contract for its body version.
- `verify_span`, the Judge and the Composer are unchanged; this ADR only supplies better
  candidates.

## Consequences

- Relationship-aware retrieval improves Evidence Precision and Information Gain without
  changing the evidence authority.
- The extractor version becomes part of provenance; changing it invalidates old offsets by
  construction.
- A broad, keyless search gap remains, honestly reported as `LIMITED` when the page is not
  found.

## Non-goals

- No embeddings/RAG, no query-specific phrase fallbacks.
- No change to the accepted evidence or database contracts.
- No named-player/named-event logic in production.
