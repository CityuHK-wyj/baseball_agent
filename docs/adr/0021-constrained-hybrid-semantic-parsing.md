# ADR 0021: Constrained hybrid semantic parsing with provenance

Status: Accepted for review on `pi/v0.1-llm-semantic-parser` (not adopted on `main`).

## Context

The deterministic analytics parser encoded natural-language understanding as ordered
regex cues with nearest-metric attachment. Independent review demonstrated repeated
*compositional* failures that no single sentence-level exception fixes:

- a qualification number (`at least 100 BBE`) becoming a pitch-velocity threshold;
- `>= 20 BBE` becoming an exit-velocity threshold while qualification fell back to 3;
- a filtered pitch velocity stealing a ranking that explicitly named maximum exit
  velocity;
- explicit exhibition population silently becoming the regular season;
- `0-2 or 1-1` being dropped or widened because a single `strikes`/`balls` pair cannot
  represent a union of exact count states.

Modifier attachment, numeric ownership and composition are exactly where unrestricted
nearest-cue parsing is fragile.

## Decision

Analytical semantics flow through a constrained hybrid parser:

```
raw query
  -> deterministic lexical pre-parse (dates, years, literals)
  -> SemanticExtractor: SemanticCandidate (closed, typed, with provenance)
  -> deterministic SemanticValidator (authoritative)
  -> canonical typed constraints
  -> Planner / Router / FieldMapping / guarded execution
```

1. **The LLM only proposes meaning.** `SemanticCandidate` is the only shape an extractor
   may emit. It is closed (`extra="forbid"`), uses closed enums and semantic keys, and
   never contains physical columns, SQL, source choice or policy. The prompt receives only
   the bounded vocabulary, never schema, rows, artifacts or SQL examples.
2. **The deterministic validator decides.** It validates vocabulary and required fields,
   grounds every explicit constraint's evidence in the raw query, detects numeric
   cross-binding (one span claimed by two clauses), and rejects contradictory
   aggregation/population/count. Only then does it emit canonical domain constraints.
3. **Extractors are interchangeable.** `DeterministicSemanticExtractor` and
   `LLMSemanticExtractor` implement one Protocol behind the existing provider-agnostic
   `ModelProvider` seam. The deterministic extractor is the tested default and the safe
   fallback.
4. **Fallback never silently weakens semantics.** A recoverable extractor/validation
   failure falls back to the deterministic extractor and records the reason. An
   unrecoverable contradiction, or an explicit analytics request the fallback cannot
   prove, fails closed into clarification rather than executing a weaker interpretation.
5. **Provenance is recorded.** Every constraint keeps `evidence_text` and, when
   available, source offsets, analogous to artifact provenance. This enables debugging,
   auditability, contradiction detection and future evaluation.
6. **Explicit values outrank defaults.** Exhibition/postseason/etc. beat the regular-
   season default; an explicit minimum beats the default 3; explicit MAX beats default
   AVG; an exact `0-2` beats any generic two-strike reading. Defaults are inserted only
   after validation confirms a dimension was absent.
7. **Compound counts are exact state sets.** `CountConstraint.states` carries the exact
   `(balls, strikes)` pairs. `0-2 or 1-1` is `{(0,2),(1,1)}` and the SQL emits an OR of
   count pairs, never an independent cartesian product.

## Consequences

- A bad LLM output can only cause a fallback or a clarification, never semantically
  wrong but technically valid SQL.
- The deterministic path still resolves the v0.1 supported grammar, so the system works
  without credentials and live verification is optional rather than required.
- `CountConstraint` gains an explicit state representation; the compact `strikes`/`balls`
  form remains for the uniform case and for backward-compatible serialization.
- Semantic observability is bounded to extractor name, parser version, fallback reason and
  a constraint summary; no raw model text or credentials are stored.

## Alternatives rejected

- **More sentence-specific regex exceptions.** Reviewer evidence shows the failure class
  is compositional, not sentence-specific.
- **Direct LLM Text2SQL or LLM source/physical-field selection.** Outside the v0.1 scope
  freeze and violates the deterministic execution boundary.
- **Passing free-form model prose downstream.** Would replace the typed domain model and
  make validation impossible.
