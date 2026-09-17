# v0.1 dual semantic runtime — implementation and release evidence

Decision context: the previous single-extractor hybrid architecture was blocked by an
independent review (`docs/reviews/v01-final-hybrid-review.md`, `FINAL_REVIEW_BLOCKED`) on
nine demonstrated containment failures. This document records the replacement dual
semantic runtime, the adversarial results and the release gates. It is implementation
evidence, not an independent review.

## Architecture

```
Raw query -> Lexical Anchors
          -> Semantic Extractor LLM (Candidate A)   \
          -> Semantic Reviewer LLM  (Candidate B)    -> SemanticReconciler
          -> Deterministic Domain Validator -> Canonical Typed Semantics -> SQL
```

* `app/semantic/lexical_anchors.py` — narrow, high-confidence facts only.
* `app/semantic/semantic_extractor.py` — `LLMSemanticExtractor` (A) and the independent
  `LLMSemanticReviewer` (B). B is never shown A.
* `app/semantic/semantic_reconciler.py` — anchor reconciliation and A-vs-B material
  comparison.
* `app/semantic/semantic_validator.py` — closed-world invariant enforcement plus anchor
  reconciliation.
* `app/semantic/dual_parser.py` — orchestration, risk-based routing, fail-safe policy.
* `app/models/semantic_review.py` / `app/persistence/semantic_review.py` — bounded
  `SemanticReviewResult` and durable reuse keyed by query + model configuration.
* See [ADR 0022](../adr/0022-dual-semantic-runtime.md).

## Mandatory gates

All commands run from the repository root.

| Gate | Command | Result |
| --- | --- | --- |
| Full suite | `python3 -m unittest discover -s tests` | 515 tests, OK |
| Compile | `python3 -m compileall -q app tests scripts` | OK |
| Secret scan | `python3 scripts/secret_scan.py` | exit 0 |
| Hybrid corpus + compositional SQL | `python3 docs/reviews/v01-semantic-hybrid-gate.py` | exit 0 |
| Final hybrid adversarial (nine containment cases) | `python3 docs/reviews/v01-final-hybrid-adversarial.py` | exit 0, `unresolved_blockers: []` |
| Dual semantic reconciliation (20 cases) | `python3 docs/reviews/v01-dual-semantic-gate.py` | exit 0, `unresolved_blockers: []` |
| Doctor / smoke preflight | `python3 scripts/smoke_test.py` | PASS |

The nine previously failing adversarial cases — contradictory numeric meaning, dropped
qualification, ranking identity, game type, exact count, omitted explicit population,
conflicting ranking limits, spelled-number qualification under provider failure, and a
suppressed location ambiguity — now reject or clarify, and their end-to-end SQL trace no
longer emits corrupted semantics.

The new 20-case dual gate covers: compound fastball/MAX-EV/qualification; AVG EV +
qualification; pitch + exit velocity in one sentence; `0-2 or 1-1`; generic two strikes;
regular season / postseason / exhibition; explicit `ALL_PITCHES`; `ALL_PITCHES` + exit
velocity incompatibility; ambiguous `high fastballs`; batter-relative upper edge; multiple
numeric constraints; spelled-number qualification; contradictory aggregation wording;
extractor failure; reviewer failure; both failures; materially disagreeing mock candidates;
and semantically equivalent candidates with different wording. For each it asserts the
reconciliation outcome, the canonical semantics or clarification, and that no explicit
constraint is silently dropped.

## Live model verification

Models: `SEMANTIC_EXTRACTOR_MODEL=deepseek-chat`, `SEMANTIC_REVIEWER_MODEL=deepseek-chat`
(separate calls and prompts). The compound query

> During the 2025 regular season, on fastballs at least 95 mph in 0-2 or 1-1 counts near
> the batter-relative upper edge, rank hitters by maximum exit velocity, requiring at
> least 20 batted balls.

was run three times with a fresh parser each time. Both roles independently arrived at
materially compatible semantics every time (no material differences), and the only
ambiguity surfaced was the documented location ambiguity, which produces the location
clarification. Evidence: `docs/reviews/dual-semantic-evidence/live-compound-repeats.jsonl`.

A run in which the extractor hallucinated an `ALL_PITCHES` population while the reviewer
did not was correctly caught as `MATERIAL_DISAGREEMENT` and clarified rather than executed.

## Real analytics E2E

Evidence: `docs/reviews/dual-semantic-evidence/live-e2e.jsonl`. All three periods produced
one canonical query per source with no second interpretation:

* 2025 — PostgreSQL: `game_type IN ('R')`, exact count union, `release_speed >= 95.0`,
  `pitch_type IN ('FF','SI','FC','FA')`, `description = 'hit_into_play'`,
  `plate_z >= sz_top - 0.25 AND plate_z <= sz_top`, `launch_speed IS NOT NULL`,
  `HAVING COUNT(*) >= 20`, `ORDER BY MAX(launch_speed) DESC LIMIT 5`.
* 2023 — Parquet: the same predicate, `HAVING COUNT(*) >= 20`.
* 2023 vs 2024 — two objectives, one routed to Parquet (2023) and one to PostgreSQL (2024),
  each with its own query.

The narrow compound query returns genuine zero qualifying rows at minimum 20 and the
objective is `FAILED`, not a false `COMPLETE`. A broader PostgreSQL query returns real
rows (`Vladimir Guerrero Jr.` 120.4 mph max EV over 108 qualifying batted balls).

## Persistence and security

* Semantic review results are persisted under the `semantic_review` object kind and reused
  on repeat/restart, so the same query is not reinterpreted and the models are not called
  again (`tests/semantic/test_dual_semantic.py`).
* A persisted disagreement/clarification is also reused, so a restart cannot bypass it.
* All previous security fixes remain in the passing suite: SQL/file/CTE escape protection,
  `baseball_readonly`, read-only transactions, DuckDB path restrictions, entity/date
  filters, bounded upper-edge predicate, game-type and batted-ball semantics,
  qualification freezing, artifact reuse, interaction replay protection, context/run
  isolation, accepted-products-only responses and demo-only synthetic data.
* The doctor confirms the live runtime identity is `baseball_readonly` with
  `transaction_read_only=on`; no secret is printed.

## Remaining DEFERRED / UNVERIFIED_LIVE

* DEFERRED: generalized temporal NLP, current-period comparisons, RAG/pgvector, additional
  statistics.
* UNVERIFIED_LIVE: full Web Evidence, the separate Operational PostgreSQL control plane,
  complete upstream pitch-grain reconciliation, and independent verification on a second
  provider/model family.
* The semantic vocabulary remains intentionally closed and narrow.
