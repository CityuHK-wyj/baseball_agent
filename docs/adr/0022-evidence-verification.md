# ADR 0022 — Evidence Verification: Relevance is not Support

Status: accepted (Phase 5B, 2026-09-25; amended by ADR 0025, 2026-09-27)

> **Closure amendment (ADR 0025).** `EvidenceSpan` now declares `source_type` and
> `retrieval_status`; only a `DOCUMENT_BODY` span of an `OK` fetched page with consistent
> offsets is admissible. Subject, year, population and required-information checks run on
> the **passage only** (a title or snippet can no longer satisfy them). Requested scope is
> classified `MATCH` / `MISMATCH` / `UNKNOWN` / `NOT_REQUESTED`: `UNKNOWN` (missing
> metadata) and `MISMATCH` (a known conflict) are distinct, and either one freezes the
> finding so the LLM verifier is **not called** and can never upgrade it. Every finding's
> `span_id` must resolve to a persisted span of an `OK` document; unresolved findings are
> dropped rather than reconstructed. See ADR 0025 for the full responsibility boundary.

## Context

The holdout audit (F4) showed Web Research reporting "4 relevant grounded sources" while
the final answer contained nothing about the requested historical event. Relevance meant
"a subject token occurs in the title or a selected sentence"; `SUCCESS` meant "some finding
is relevant". A page mentioning MLB was treated as evidence about a particular MLB
historical event, a page mentioning a player and an injury as evidence of an injury in the
requested season, and an interview plus a statistical change as proof of causation.

The Phase-4 `EpistemicStatus` labels (`SUPPORTED / INFERRED / BACKGROUND / HYPOTHESIS /
UNKNOWN`) are preserved unchanged. They describe how a statement may be presented in the
answer, which is a different question from whether a fetched passage establishes a specific
obligation.

## Decision

Keep three retrieval levels distinct and make the decision explicit:

1. **SearchHit** — a search-engine result. Never grounds a claim.
2. **RetrievedDocument** — a page that was actually fetched, with URL, provider, title,
   publication date, retrieval time, retrieval status, content hash and a bounded excerpt.
3. **EvidenceSpan** — an inspectable passage with text and offsets in its document.
4. **EvidenceFinding** — the result of verifying one span against one `AnswerProposition`
   (`SUPPORTED / CONTRADICTED / INSUFFICIENT / IRRELEVANT`), with what the passage actually
   establishes, the verifier (`deterministic`/`llm`), the checks and a rationale.
5. **Claim** — unchanged: a statement eligible for the final answer.

**Why existing contracts cannot carry this.** `Artifact` and `Reference` identify *that*
something was retrieved but have no per-passage decision field. `Claim` is the answer
statement; using it to record a rejected passage would conflate "we found this" with "we
may say this". `EpistemicStatus` cannot express `INSUFFICIENT`/`IRRELEVANT`/`CONTRADICTED`
for a candidate passage without becoming a claim. So a dedicated verification result is
necessary; it is a transient decision record, not a new authority.

Verification is layered:

- **Deterministic** where reliable: canonical subject must appear in the *passage* (a title
  alone is not enough), requested year must match (a conflicting year fails, and a missing
  year is `UNKNOWN` rather than a match), requested population must match, and the passage
  must share content tokens with the required information (inflections allowed). A
  textually anchored negation/contrast records `CONTRADICTED`. A deterministic hard gate
  short-circuits the LLM entirely.
- **LLM semantic verification** when understanding a passage is required, asking only what
  the passage establishes under the requested scope. Model confidence is never treated as
  independent evidence, and a verifier failure can never create support.

A URL or snippet alone never manufactures a document or a span.

## Retrieval outcomes

`web_research` distinguishes and reports:

`SEARCH_BACKEND_UNAVAILABLE` (provider failed) · `NO_RESULTS` · `SOURCE_UNAVAILABLE` (hits,
no page) · `IRRELEVANT_RESULTS` (pages, no subject) · `INSUFFICIENT_EVIDENCE` (subject
present, no obligation established) · `PARTIAL_EVIDENCE` · `OK` (all mandatory prose
propositions supported) · `CONFLICTING_EVIDENCE` (support and contradiction retained).

`SUCCESS` now requires at least one proposition to be supported by an inspectable span.
Failed fetches are still recorded as documents so the failure is auditable.

## Consequences

- Confidence and fluent wording are never evidence.
- Two disagreeing sources keep the conflict; the Judge records `PARTIAL`/`CONTRADICTED`
  rather than silently selecting a convenient answer.
- `SEARCH_QUERY`/`WEB_EVIDENCE`/Binder contracts are unchanged; the richer structure lives
  in the artifact payload and its evidence records.
- Improved verification does **not** guarantee retrieval. If the right page is not fetched,
  the outcome is a typed, honest insufficiency — a Web Research v2 retrieval-capability gap.
