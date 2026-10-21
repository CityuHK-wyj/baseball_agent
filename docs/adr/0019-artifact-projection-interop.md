# ADR 0019 — Explicit, Lineage-Preserving Artifact Projection

Status: accepted (Phase 5A, 2026-09-21)

## Context

An analytics result often contains the subject a later tool needs (the top-ranked hitter, a
player with the largest change, a specific row). But the Binder is deliberately typed:
`web_research` and `entity_resolution` consume `SEARCH_QUERY`; `local_analytics` produces
`RANKED_ENTITY_SET` / `STATISTICAL_RESULT`. When the Planner asked entity resolution or web
research to consume a rank artifact directly, the Binder correctly rejected it with
`EXPORT_TYPE_INCOMPATIBLE` (holdout F2). The failure was the absence of a converter, not an
over-strict Binder.

## Decision

Add one minimal, general converter capability, `artifact_projection`, and keep the Binder
strict. It performs no LLM call and never invents an identity.

Input: a bound `RANKED_ENTITY_SET`, `STATISTICAL_RESULT` or `DERIVED_MEASURE`.
Selection: an explicit, recorded `SelectionRule` (`TOP` / `INDEX` / `ENTITY_ID`); the
default is the first row of the artifact (already ordered by its analytical sort).
Output: an `ENTITY_PROJECTION` artifact exporting

- `SEARCH_QUERY` (searchable subject: name, optional canonical id, query),
- `ENTITY_MAPPING`,
- `PLAYER_ID_SET` when a canonical id is present,

each carrying `source_artifact_id`, `source_export_id`, `selected_row_identity`,
`selection_rule`, `canonical_id`, `namespace`, `searchable_name`, `original_scope` and
`lineage`. The artifact's `lineage` includes the source artifact and export.

The runtime reads a bound `SEARCH_QUERY` / `ENTITY_MAPPING` when building an
`entity_resolution` or `web_research` request, so the projected subject reaches the consumer.

## Invariant

Cross-tool conversion is explicit, typed and lineage-preserving. A Binder rejection is
correct when no converter exists; the fix is to add the converter, never to relax the
Binder or relabel one export type as another.

## Consequences

- A verified database result becomes a legitimate investigation input without weakening
  any contract.
- No entity id is fabricated: projection only reads ids already present in the source row.
- Narrative/statistical separation is preserved; text is never relabelled as a statistic.

## Phase 5A closure addendum (independent revalidation)

Four safety/correctness gaps were closed:

1. **Default TOP requires verified ordering.** `TOP` is only accepted when the source
   artifact carries a verified analytical ordering (`EnumerationContract.ordering_verified`
   plus a non-empty ordering, or an export contract with `verified_ordering`), or when the
   source has at most one row. An unsorted multi-row artifact requires an explicit
   `INDEX` / `ENTITY_ID` selection; otherwise the capability fails with
   `UNVERIFIED_ORDERING`.
2. **ID domain proof.** Only the catalog-validated `batter_id` / `pitcher_id` /
   `player_id` / `mlbam_id` columns (or an artifact whose enumeration declares
   `entity_kind=PLAYER` / `namespace=MLBAM`) produce a canonical id and a `PLAYER_ID_SET`.
   A generic `id` / `entity_id` is recorded as a raw, unverified identifier and can never
   become a canonical MLBAM id or a `PLAYER_ID_SET`.
3. **Full per-export provenance.** Every produced export (including `PLAYER_ID_SET`) carries
   the source artifact/export id, selected row identity, selection rule, original scope and
   lineage.
4. **Deterministic runtime converter discovery.** When a consumer declares an input type its
   dependency cannot supply (`EXPORT_TYPE_INCOMPATIBLE`), the runtime inserts a registered
   converter capability between them. The Planner does not have to discover the converter,
   and the Binder is still not relaxed.
