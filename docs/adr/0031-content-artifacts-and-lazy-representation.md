# ADR 0031 — Content Artifacts and Lazy Representation

Status: accepted (Web Research v2 architecture, M2.9)

Architecture reference: `docs/architecture/WEB_RESEARCH_V2.md` §3, §6. Replacement target:
the source-category retrieval contracts that M2.8/M2.8A showed do not generalize.

## Context

M2.8 and M2.8A progressively typed external resources by source category (feeds, local
media, community sites, interviews, publishers, aggregators). Broader discovery increased the
number of retrieved bodies but did not produce demonstrated source-specific coverage gains,
and it exposed additional evidence-scope failures. Modeling every website family as its own
retrieval contract is not scalable: the open web is an open-world input boundary, and a
resource must not have to fit one exhaustive semantic schema before it can be used.

## Decision

Web Research acquires **content artifacts** and preserves their **representations**.

1. **ContentArtifact is thin.** The existing `RetrievedDocument` is the concrete content
   artifact: identity, source URI, acquisition time, provenance, retrieval status, and an
   optional acquisition metadata block. No new `ContentArtifact` class was introduced, so
   there is one ownership model. Semantic fields (`title`, `publication_date`, author,
   speaker, type) remain optional and are never fabricated.

2. **Representation is the extensibility mechanism.** `Representation` is a minimal envelope:
   `id`, `kind`, `media_type`, `content_ref`, `content`, `content_hash`,
   `acquisition_method`, `derived_from`, `derivation_method`, `derived_by`,
   `structured_data`, `locator_type`, `provenance`. A resource may expose zero, one, or many.

3. **Native structure is preserved.** JSON stays `JSON` with parsed `structured_data`; XML/RSS
   stays `XML`/`FEED_ITEM`; HTML stays `HTML`; browser output is `DOM`. The extracted plain
   text is a `TEXT` representation **derived from** the native parent, not a replacement. No
   representation is declared universally canonical.

4. **Missing information stays missing.** Absent author/date/speaker/title remains absent.
   Only an explicit, traceable derivation may add a value, and it records `derived_from`,
   `derivation_method`, `derived_by` (`source` | `deterministic` | `llm`) and a content hash.
   LLM/interpretation output can never overwrite a source or deterministic representation.

5. **Lazy interpretation.** Structured evidence is bound only when a proposition is being
   evaluated. A `Representation` is exposed as a payload-free summary first
   (`content_artifact_summary`/`representation_index`); the full body is loaded by the step
   that needs it. No eager entity/quote/author/classification extraction runs at ingestion.

6. **Evidence binds to a representation.** `EvidenceSpan.representation_id` names the
   concrete view. Text spans keep the exact `representation[start:end] == span.text`
   contract. Structured spans use `source_type=STRUCTURED`, `locator_type=json_pointer`, and a
   stable `locator`; replay checks `canonical_json(resolve(pointer)) == span.text`. Structured
   data is never degraded into artificial prose offsets. The existing `EvidenceSpan`,
   `EvidenceFinding`, verifier and Judge contracts are reused, not duplicated.

7. **Authority is separate from representation.** `Representation.kind` never changes trust.
   Trust stays with the source identity/`SourceDescriptor` and the evidence policy. Provider
   transport (`tavily`, browser, HTTP) is recorded on the representation, but the factual
   source is the URL.

## Invariants

- Missing optional metadata never blocks ingestion and is never synthesized.
- A derivation never mutates its parent; reverting to an earlier representation is always
  possible.
- Every derived representation has a resolvable `derived_from` and a derivation method.
- Structured evidence replays through its JSON Pointer; prose evidence replays through its
  offsets.
- Duplicate representations of one source are recognized by canonical content, not counted
  twice.
- Provider-specific objects do not leak into SpanSelector, Verifier, Judge or Composer.
- The M2.7 relation-direction gate still runs on every admitted span.

## Consequences

- New resource kinds enter through the same `Representation` envelope without a new retrieval
  branch.
- The legacy `body_text`/offset path remains the compatibility boundary; existing tests and
  live evidence behavior are unchanged when no native structure exists.
- Official JSON/XML can ground evidence as structured records without being flattened into
  prose.
- A future representation type (transcript, table, entity-annotated view) can be added
  without changing Planner/Judge contracts.

## Non-goals

- No universal webpage ontology and no article-shaped requirement for every artifact.
- No source-specific architecture per website category.
- No new paid provider, no new Agent, no M3 work.
- No change to the evidence verifier's semantic authority.
