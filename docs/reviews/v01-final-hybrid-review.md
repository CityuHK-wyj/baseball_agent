# Independent final hybrid review

Decision: **FINAL_REVIEW_BLOCKED**. No adoption, main update or tag.

Implementation reviewed: `pi/v0.1-llm-semantic-parser` at
`ebdccc75793ebd2cf448e1b199f2a16109e5ca7e`, tree
`825ee92334ec4e1237fcd5e001816f9ae72f0cfd`.
Review branch: `codex/v0.1-final-hybrid-review`, created directly from that SHA.
The diff base was independently verified as `eae9875e86632f509b0351ea31da040ef151bc7e`.
Only review evidence, a failing adversarial gate and documentation changed in this review.
The final review commit/tree can be obtained with `git rev-parse HEAD HEAD^{tree}`;
its hashes are reported separately to avoid a self-referential committed hash.

## Findings and exact reproduction

Run `python3 docs/reviews/v01-final-hybrid-adversarial.py`.
It exits **1**, with nine unresolved cases. This is deliberately separate from the
passing application suite; its failure must not be hidden by the suite count.
See [recorded results](final-hybrid-evidence/adversarial.jsonl).

### P1 — Grounded text does not ground the proposed meaning

`app/semantic/semantic_validator.py:79` checks substring/offset agreement, but
`_canonical` accepts the model's values without independently reconciling them with
the actual clause. Genuine evidence is enough to authorize incorrect canonical intent:

| Evidence | Accepted incorrect candidate |
| --- | --- |
| fastballs at least 95 mph | pitch_velocity LTE 20 |
| at least 100 BBE | qualification 3 |
| top 5 by maximum exit velocity | AVG pitch_velocity, limit 50 |
| exhibition games | REGULAR_SEASON |
| 0-2 or 1-1 counts | exactly 3-1 |

The gate traces a combined wrong candidate through canonical constraints, the
Requirement and its execution descriptor into SQL: `release_speed <= 20.0`,
`HAVING COUNT(*) >= 3`, `ORDER BY AVG(launch_speed) DESC`. These contradict the
requested >=95, >=100 and MAX. The physical adapter faithfully executes the corrupted
canonical meaning; it does not repair it. No database write or SQL injection is needed.

**Next action:** deterministically reconcile each supported explicit dimension and
its numeric/operator ownership with the original request before accepting a proposal.
Unknown or conflicting interpretations must clarify. Model-provided origins and
evidence cannot establish that a dimension was absent or that a proposed value is true.

### P1 — Explicit population disappears, including with a real model

Query: `Rank hitters by maximum exit velocity over all pitches in 2025`.
The independent `deepseek-chat` call returned a ranking but omitted population.
`validate_candidate` at lines 319–321 supplied BATTED_BALL, without clarification.
[Exact single-call candidate and canonical result](final-hybrid-evidence/live-contained.jsonl).
The adversarial gate reproduces the same failure offline.

The explicit ALL_PITCHES candidate path itself correctly clarifies: the recoverable
error falls back to deterministic extraction, which retains ALL_PITCHES and rejects
the EV incompatibility. That protection is bypassed by omission, or a model-proposed
wrong default. Thus metric-compatible defaults are acceptable policy in principle,
but this implementation has not proved the user left the dimension unspecified.

**Next action:** check explicit population in the query independently of candidate
presence, and never normalize away an explicit incompatible population.

### P1 — Location proposal suppresses its own ambiguity

Query: `Show me hitters against high fastballs.` A real model returned both
BATTER_RELATIVE_UPPER_EDGE grounded only in `high` and a `location.upper_edge`
ambiguity. Both were accepted. At validator lines 323–325, any LocationConstraint
suppresses `location_wording_requested`. The live lifecycle separately recorded
`needs_clarification=false`. The offline gate reproduces the conflicting candidate.
See [live gate evidence](final-hybrid-evidence/live-llm.jsonl).

**Next action:** unresolved ambiguity must block execution even when a candidate
also proposes a location. Independently recognized ambiguous wording cannot be
resolved by a model claiming USER_EXPLICIT.

### P1 — Provider failure silently weakens qualification

With an extractor raising SemanticProviderError, query
`top 5 by maximum exit velocity with at least twenty batted balls in 2025`
returns ranking/population but no qualification and no clarification. The normal
requirement policy then supplies default 3. A successful live model interpreted
twenty as 20, so provider availability changes the question answered.
`app/semantic/hybrid_parser.py:62` only checks whether *all* constraints are absent;
it does not detect partial loss. Spelled numbers need not be supported to be safe:
clarification is sufficient.

**Next action:** prove supported-clause coverage before fallback execution; unresolved
explicit restrictions must fail closed even when other clauses parsed successfully.

### P2 — Rank conflicts and provenance are insufficiently represented

`top 5 or top 10 by maximum exit velocity in 2025` silently selects 5 under provider
failure. Ranking contradiction checks compare metric/aggregation, not limit/direction.
DEFAULT_RANKING_LIMIT is correctly owned by domain policy, but its injected value
inherits USER_EXPLICIT from the whole ranking, so default versus explicit limit is
not independently represented. `SemanticNormalizer` uses `parsed.constraints` and
summary notes but discards `parsed.provenance`; evidence spans do not persist into
the objective/requirement lifecycle. These are auditability and intent defects,
not a reason to redesign the remaining frozen layers.

## Standards

The grounding and fallback findings violate ADR 0021's deterministic-authority and
explicit-intent contract and CONTEXT's Semantic Validator definition. Evidence
provenance is also lost after normalization. Two documented-boundary findings;
the first is P1. No cosmetic smell-based refactor is proposed.

## Spec

Four P1 categories above violate stochastic containment, explicit population
precedence, ambiguity handling and fallback fidelity. One P2 category covers rank
conflicts/default provenance. Passing provider examples do not discharge these
requirements. No feature expansion was added. Five finding categories; worst P1.

## Gates and limits of their assertions

- Baseline **479 tests, no skips**; final **479 tests, no skips** (20.240 seconds).
  [Final suite output](final-hybrid-evidence/tests.txt). Compileall and secret scan passed.
- Original blocker reproduction: all five resolved. NL-to-SQL intent trace: all
  cases passed. Hybrid gate: 23 corpus cases and five compositional SQL cases passed.
  Both PostgreSQL and Parquet SQL preserve the historical five repaired cases.
- New adversarial gate: nine failures, grouped above. Unsupported physical metric,
  invalid count/aggregation and qualification-only velocity evidence reject correctly.
  Existing suite also exercises duplicate evidence ownership, unknown schema fields,
  provenance mismatch and provider schema failure.
- The hybrid corpus's parse assertions do not validate every dimension; e.g. its
  population check compares game types, not event population. The live gate returns
  success based on compound consistency, without asserting its recorded ambiguity
  lifecycle. It returned 0 despite `needs_clarification=false` in this review.
- The original live-hybrid E2E script uses the deterministic default pipeline and
  sometimes infers source labels from years. The additional
  [live-pipeline.py](final-hybrid-evidence/live-pipeline.py) injects the real provider
  and records actual executor calls, avoiding reliance on those labels.

## Independent live results

**LLM: LIVE_VERIFIED for deepseek-chat transport and extraction, containment FAILED.**
The five representative cases were validated and the compound canonical meaning
was correct on 3/3 repeats. This does not establish global safety: the real explicit
population and ambiguous-location counterexamples above crossed validation.
The separate three-call probe validates precisely the same candidate it records.
The default deepseek-v4-pro probe was stopped after over six minutes without a
completed output record; independent default-model verification remains UNVERIFIED_LIVE.
No credential, provider raw response or hidden reasoning is recorded.

**Analytics PostgreSQL and Parquet: LIVE_VERIFIED.** The requested narrow 2025 and
2023 compound queries produced genuine zero qualifying rows at minimum20, with
correct date, count union, GTE95 pitch filter, regular-season code R, in-play
description, bounded upper edge, non-null EV, MAX ordering and limit5. ToolResult
is OK with an empty Artifact; the objective remains FAILED, not false COMPLETE.
Cross-source 2023/2024 preserves separate objectives and actual source calls.
The full provider-to-runtime probe records the same stages and SQL in
[live-pipeline.jsonl](final-hybrid-evidence/live-pipeline.jsonl).

Additional previously defined broad controls returned real accepted products:
historical-wide COMPLETE; recent-wide LIMITED; regular-season 2023 vs 2024
without narrow pitch/location restrictions COMPLETE/COMPLETE with separate products.
These controls are separate queries, not substitutions for the requested narrow one.
No synthetic fallback was observed. SQL's simultaneous AVG/MAX columns remain
explicitly labeled in payloads; ordering comes from the canonical ranking aggregation.

Population filtering remains `description='hit_into_play'` for BATTED_BALL;
MEASURED_CONTACT uses measured launch_speed and ALL_PITCHES adds no contact predicate.
EV additionally requires non-null launch_speed; qualification counts measured rows
after requested filters, not all plate appearances. Game types remain deterministic.

Fresh read-only data audit reproduced Parquet **6,168,817** rows, 2015-04-05 through
2023-11-01; PostgreSQL **2,196,186**, 2024-03-15 through 2026-09-14. Both have 100%
populated game_type, zero exact-row duplicate excess and no mismatches against the
retained gamePk map. This is not complete pitch-key reconciliation; the archive lacks
canonical pitch keys. Fresh upstream schedule re-download was not repeated this pass.

**Security/persistence: prior protections remain intact in tested scope.** No app
security executor or SQL-guard changes in this diff. Full suite includes SQL/file/CTE
escapes, role restrictions, interactions, artifact reuse, evidence isolation and
backfill preflight regression coverage. Live role is baseball_readonly and transaction
read-only is on. CREATE/INSERT/UPDATE/DELETE/DROP each failed with SQLSTATE25006;
every probe rolled back. Six real restart controls preserved objectives and executed
zero additional source queries. No distributed exactly-once guarantee is claimed.

## Documentation, scope and next checkpoint

Current status entry points now point here. The prior five compositional blockers
are repaired; the new blockers concern containment, not those old reproductions.
Historical implementation reports remain evidence of their original claims and are
superseded by this independent verdict. Broad claims of safe fallback or fully
authoritative semantic validation are not release-verified.

DEFERRED: generalized temporal NLP, current-period comparisons, RAG/pgvector,
new agents/infrastructure and additional statistics. UNVERIFIED_LIVE: full Web
Evidence, Operational PostgreSQL, complete upstream pitch-grain reconciliation,
and independent default deepseek-v4-pro verification. Analytics PostgreSQL is distinct
from Operational PostgreSQL.

Remote main was independently checked as
`c93953d4c7e54dfd2b98ffd7d559e5efc946e6b9`; no `v0.1.0` tag existed. No force push,
unrelated-history merge or main adoption was attempted. Fix the demonstrated semantic
boundary defects and rerun this gate before another independent review. Only after
approval should one adoption commit use the exact reviewed tree and current safe
main as sole parent, prove tree equality/empty diff, rerun gates on the adoption
commit, recheck remote main and perform a normal fast-forward and annotated tag.
