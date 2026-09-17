# ADR 0022: Dual semantic runtime — independent proposal, independent review

Status: Accepted for the v0.1 dual semantic runtime.

## Context

ADR 0021 introduced a constrained `SemanticCandidate` proposed by one LLM and accepted or
rejected by a deterministic validator. Independent adversarial review showed this is not
enough: the validator proved *schema* fidelity but not *semantic* fidelity. A single model
could emit evidence-grounded but wrong meaning, and the deterministic validator accepted
it:

- `at least 95 mph` became `pitch_velocity LTE 20`;
- `at least 100 BBE` became `qualification 3`;
- `top 5 by maximum exit velocity` became `AVG pitch_velocity, limit 50`;
- `exhibition games` became `REGULAR_SEASON`;
- `0-2 or 1-1` became `3-1`;
- an explicit `ALL_PITCHES` population was omitted and silently replaced by the default;
- a materially ambiguous `high fastballs` location was overwritten by a model choice;
- a provider failure dropped `at least twenty batted balls` and used the default
  qualification.

The common root cause is that deterministic code cannot fully understand arbitrary
English, and one model must not both propose and approve its own interpretation.

## Decision

Analytical semantics flow through a **dual semantic runtime**:

```
                     Raw User Query
                           |
                           v
                   Lexical Anchors
              dates / numbers / units /
               explicit literals only
                           |
             +-------------+-------------+
             |                           |
             v                           v
     Semantic Extractor LLM      Semantic Reviewer LLM
             |                           |
       Candidate A               Independent Candidate B
             |                           |
             +-------------+-------------+
                           |
                           v
                  Semantic Reconciler
                           |
              +------------+------------+
              |                         |
              v                         v
       materially agree          material disagreement
              |                         |
              v                         v
 Deterministic Domain Validator      Clarification
              |
              v
     Canonical Typed Semantics
              |
              v
   Planner → Router → Tools → Data
```

1. **No single model may both propose and approve meaning.** The extractor (`LLM A`) and
   reviewer (`LLM B`) are separate roles with separate prompts and separately configurable
   models. The reviewer is never shown Candidate A; it must independently answer "what does
   the user mean?".
2. **Both roles emit the same closed typed schema.** `SemanticCandidate`,
   `EvidenceSpan`/`SemanticProvenance`, `CountState`, `NumericConstraint`,
   `PitchTypeConstraint`, `LocationConstraint`, `RankingConstraint`,
   `QualificationConstraint`, `PopulationConstraint` and `TimeRange` are closed enums with
   `extra="forbid"`. Neither candidate may contain SQL, physical columns, table/database
   names or executable expressions.
3. **A first-class `SemanticReconciler` compares canonical meaning, not wording.** Evidence
   offsets, ordering, equivalent synonyms and default-equivalent dimensions are
   non-material and reconcile automatically. Material differences (metric, aggregation,
   game type, exact count states, qualification present/absent, location definition) are
   never resolved by code or a third model: they become a Clarification and the user is
   the semantic authority.
4. **Lexical anchors are narrow supporting evidence, not the source of truth.**
   `app/semantic/lexical_anchors.py` detects only high-confidence facts: explicit
   years/dates, numeric literals with units, count literals, ranking limits, population
   wording, known pitch families and explicit location definitions. The deterministic
   validator reconciles every candidate against them: a value that *contradicts* what the
   user wrote is rejected, and an explicit restriction the candidate *dropped* fails
   closed. Spelled numbers are detected but deliberately not converted, so the
   deterministic path clarifies rather than guessing. The anchors are not a rebuilt
   natural-language parser.
5. **Explicit user semantics outrank defaults, but a single model's claim of
   `USER_EXPLICIT` is not sufficient.** Defaults are inserted only after reconciliation
   confirms the dimension was not explicitly requested.
6. **Provider-failure policy is fail-safe.**
   * one role fails — the single interpretation does not receive independent model
     approval; the deterministic high-confidence path is used only if it proves every
     explicit restriction, otherwise the request clarifies;
   * both roles fail — fail closed into clarification.
   Explicit constraints are never silently dropped and defaults are never silently
   applied.
7. **Risk-based routing is deterministic and documented.** Dual review is required when the
   query carries an analytical cue (numeric threshold, qualification, count, ranking,
   population, pitch family or ambiguous location). Plain knowledge/entity questions (for
   example `DFA是什么意思？`) do not make two analytics model calls.
8. **Compound counts stay exact state sets.** `0-2` is `{(0,2)}`, `two strikes` is the
   generic valid two-strike set, `0-2 or 1-1` is `{(0,2),(1,1)}`; never independent
   ball/strike cartesian products.
9. **Review is a bounded structured artifact.** `SemanticReviewResult` stores the two
   candidates, agreement status, material differences, ambiguities, the canonical
   candidate (only when safe), models, per-call latency and timestamps. It never stores
   hidden model reasoning, prompts, SQL or credentials.
10. **Review survives restart.** `SemanticReviewStore` persists the result keyed by
    `sha256(raw_query + extractor_model + reviewer_model)`. A restart or repeat request
    reuses the approved reconciled semantics (or the recorded clarification) instead of
    calling the models again.
11. **Final authority stays deterministic.** The deterministic domain validator still
    enforces closed-world invariants: enum validity, balls 0–3, strikes 0–2, known
    metrics, valid operators, positive qualification, metric/population compatibility,
    prohibited physical fields and SQL, and system policy. It enforces invariants, not
    open-world language comprehension.
12. **No LLM Text2SQL.** LLMs produce typed semantics only; the Planner/Router and guarded
    read-only adapters produce and execute SQL.

## Consequences

- The nine demonstrated adversarial containment failures now clarify or reject instead of
  reaching SQL, and the previously repaired compositional cases stay repaired.
- Two model calls are made for analytical queries, so the semantic roles default to a fast
  interactive model and are configuration-driven. Latency and outcome are recorded.
- The semantic layer is more conservative: some queries that a single model would have
  answered now clarify. This is the intended correctness/latency trade-off for v0.1.
- The provider abstraction is unchanged (`ModelProvider`); replacing DeepSeek with another
  provider is a composition/configuration change, not a domain change.

## Alternatives rejected

- **Majority vote by a third LLM.** Adds a model without independent authority and hides
  disagreement instead of surfacing it.
- **Asking the reviewer "is Candidate A correct?".** Anchors the reviewer on the
  extractor's mistakes; the reviewer must first reconstruct meaning.
- **Trusting a single model when the other fails.** Reintroduces the original failure mode
  under load/outage.
- **Teaching the deterministic layer to parse spelled numbers or arbitrary phrases.**
  Rebuilds the fragile NLP layer the anchors deliberately avoid; clarification is safer.
