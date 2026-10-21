# ADR 0018 — Contextual Entity Reference is Provenance-Bearing and Monotonic

Status: accepted (Phase 5A, 2026-09-21)

## Context

The semantic interpreter understands a follow-up ("他呢？", "那去年呢？") and may name the
carried entity explicitly in its `understanding` or in an `entity` constraint. Nonetheless,
the runtime built `UserRequirement`s and `entity_resolution` requests only from
`SemanticInterpretation.entities`, which often still contained the literal surface pronoun. A
verified canonical identity therefore regressed to an unresolved surface mention at the
semantic → goal → Need → ToolRequest boundary (holdout F1). A dictionary miss then produced
`CANDIDATE_ONLY` / `UNKNOWN`, and downstream database work blocked with
`EMPTY_ENTITY_SET` / `UNRESOLVED_REQUIRED_INPUT`.

The defect was not a dictionary miss. It was a dataflow failure: the canonical identity
recognized at the semantic layer had no representation that survived to entity resolution.

## Decision

Introduce a first-class `EntityReference` that is built deterministically from the semantic
message plus accepted conversation context, *before* requirements are constructed, and is
carried by the ENTITY `UserRequirement` and the `entity_resolution` ToolRequest.

An `EntityReference` preserves, simultaneously:

- `surface` — the user's original wording, including a pronoun;
- `canonical_id`, `canonical_name`, `namespace` — the identity, when known;
- `status` (`CANDIDATE` / `RESOLVED` / `CANONICAL`) and `identity_verified`;
- `source` (`user` / `interpreter` / `context` / `resolution`);
- `antecedent` and `carried_from_goal` — the reference provenance;
- an embedded `Reference` (user span).

Resolution rules (`app/semantic/entities.py`):

1. A pronoun or `carried` mention resolves its antecedent only from **accepted context**
   (the parent Goal's entity references, else the session's recorded canonical entities).
2. It resolves only when there is **exactly one** plausible antecedent. More than one is
   genuine ambiguity: the reference is marked `ambiguous`, left unresolved, and the runtime
   asks the user instead of guessing.
3. A model-provided entity constraint name attaches to the pronoun as a `RESOLVED` name,
   never as a fabricated canonical id. A canonical id is only adopted when it came from a
   verified resolution or accepted context (`identity_verified`).
4. `entity_resolution` short-circuits a `CANONICAL`/`RESOLVED` contextual reference with a
   canonical id, recording `source="context"`; a carried name (no id) is used as the lookup
   key even when the surface is a pronoun.
5. Turn classification is upgraded to `FOLLOW_UP` when the interpreter reports a follow-up
   or the turn contains a contextual reference, so long follow-ups are no longer missed by
   the short-message heuristic. Independent turns still inherit no prior constraint.

## Invariant

A resolved contextual reference cannot regress to an unresolved pronoun at any runtime
boundary, and no unverified candidate may masquerade as a canonical id.

## Consequences

- Follow-ups keep both the original surface and the canonical identity end to end.
- True ambiguity produces a clarification, not an invented identity.
- The Planner prompt is not relied on to "remember pronouns"; the data flow carries it.
- Independent questions remain context-free because only semantic (current-turn) constraints
  become requirements.

## Phase 5A closure addendum (independent revalidation)

Two gaps were closed after the incremental revalidation:

1. **Independent-turn isolation.** A turn classified `INDEPENDENT_GOAL` is upgraded to a
   follow-up only on a deterministic signal: the short-message follow-up classifier, or a
   contextual surface/carried mention in the current text. The interpreter's own
   `FOLLOW_UP` label is not sufficient by itself. Independent turns are built with no
   conversation context, and `strip_context_contamination()` removes any entity/constraint
   that exactly matches a prior context identity but is not anchored in the current user
   text. This prevents a model-generated stale entity constraint from leaking into an
   entity-free independent question.
2. **Ambiguity options.** An ambiguous contextual reference now records its candidate
   antecedents in `EntityReference.options`, and the clarification surfaced to the user
   lists them instead of an empty option set.

The full Semantic -> Goal -> Planner -> ToolRequest -> entity_resolution path is exercised
by Engine-level tests (`tests/test_phase5a_closure.py`).
