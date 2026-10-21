# ADR-draft 04 — Authority Ancestry, Lexical Cues, and Derived Computation

Status: **accepted architectural decision; staged implementation in progress.**
Relates to ADR 0003 (Requirement vs Need), ADR 0014 (hypothesis/evidence/claim), ADR 0016
(semantic completion), M2.12R3 authority resolution, WSA-01/02/03, WSA-07.

## Context

- `RequirementBuilder` marks Requirements `source="user"` from interpreter output without a
  deterministic user-span ancestry check; assistant synthesis can become a user Requirement
  (F-04).
- `derive_authority_expectation`/`derive_assertion_expectation` and `temporal.information_class`
  derive authority-relevant expectations from lexical cue tuples; a negated cue ("NOT fan
  reaction") weakens an OFFICIAL_STATE requirement (F-05).
- `python_compute` output becomes a `DERIVED_MEASURE` and the Judge mints a SUPPORTED Claim
  from any non-empty rows, even when the model authored a constant (F-10).
- `dynamic_fact` copies a caller `as_of` and exposes an untyped relation (F-02).

## Decision

1. **Requirement ancestry is proven, not asserted.** Every user-authoritative Requirement
   carries RequirementProvenance rooted in the current bound user message, a backend-validated
   clarification delta, or governed contextual derivation. Distinguish current user,
   clarification, derived context and assistant/context ancestry. A user_span proves ancestry,
   not semantic correctness; normalized paraphrases need not literally equal a substring.
   Assistant history may supply identity/coreference, never factual Requirement authority.
2. **Lexical cues never set expectation authority.** Authority/assertion/temporal expectations
   come from the governed Requirement intent and stable Shared Knowledge, or from a typed
   clarification. Cues may only guide candidate generation; a negated or ambiguous cue must not
   loosen a requirement. Loosening requires a typed intent, not a substring match.
3. **Derived computation is a governed operation.** A derived result carries an
   `ArtifactAssessment`: approved operation kind, factual operand references
   (artifact + field/measure + row scope), output field semantics, and a replay hash. The Judge
   mints a derived Claim only when the lineage validates; a constant or an untraceable output is
   not admissible as a factual Claim. AST safety proves execution safety only. Validate bounded
   actual operation classes, operand fields/rows/populations, output semantics and replay;
   unknown operations remain candidates. Preserve arithmetic, rates, differences, rankings,
   aggregations, windows and governed cross-artifact computation.
4. **Syntactic operand access does not prove semantic output dependency.** A program that
   reads a factual operand and then returns an unrelated constant, categorical value, medical
   status or third-party ranking has operand access but no output dependency. Replaying such a
   program only reproduces the fabricated result, so replay alone is never grounding. A factual
   derived Claim reverse-traces through `Claim -> derived output field -> output-specific
   dependency proof -> governed operation -> exact factual operands -> operand
   field/measure/row scope -> operand authority ancestry`; the weaker `program accessed valid
   input + program emitted output` chain is insufficient. Free-form `python_compute` output that
   cannot be proven to correspond to a supported governed operation may remain a candidate
   analytical artifact but must never automatically become a SUPPORTED Claim. Require exact
   operation correspondence (requested vs executed vs output semantics): the same operands do
   not imply the same analytical meaning, so a RATIO/SUM/MAX/arbitrary program cannot satisfy a
   difference Requirement merely by reading the same operands.
4. **Structured records are proposition-local.** A structured external record must expose typed
   relation, subject identity, provider record identity, as_of/event time and source role, and
   is admitted only through the target proposition's deterministic compatibility gate.

## Invariants

- Provenance is not semantic proof; a span reference proves the wording, not entailment.
- Mutable Shared Knowledge cannot satisfy a current factual Requirement.
- `kind == DERIVED_MEASURE` never implies factually grounded; `kind == DYNAMIC_FACT` never
  implies semantic relevance.
- `input access != output dependency` and `program replayability != factual grounding`. A
  factual derived output field must have an output-specific proof over a governed operation.
- No stage may weaken authority enforcement to improve execution rate.

## Consequences

- F-02/F-04/F-05/F-10 close at their semantic owners, not downstream.
- Positive controls (legitimate paraphrase/reporting, valid derivations, current-team lookups)
  must remain green and are regression gates.

## Non-goals

- No vocabulary/regex accretion to patch individual phrasings.
- No change to `resolve_authority`'s role as the analytical authority resolver; the new
  provenance feeds it.
