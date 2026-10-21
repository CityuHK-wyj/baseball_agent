# Natural-Language Hardcoding Audit

Date: 2026-10-05
Milestone: M2.12R4 (pre-freeze semantic robustness)
Author: semantic-layer audit for the Baseball Agent rebuild
Status: audit complete; highest-impact architectural instance repaired and live-validated

## 0. Executive summary

The semantic/analytics layer mixed two fundamentally different kinds of rule:

1. **closed-world execution rules** — governed enums, schema fields, operators, Measures,
   populations, canonical IDs. These are legitimate and must stay deterministic; and
2. **open-ended language interpretation rules** — hand-written phrase lists and growing
   regex alternations that decided *factual* semantics (BATTER/PITCHER, filters, grouping,
   limits, qualification) directly from the user's wording.

Category (2) is the architectural defect. It made recall depend on an ever-growing,
hand-enumerated vocabulary and made factual correctness depend on brittle wording
(`hitting`, `how did … hit`, `totals`, `LHP`, `southpaw`, Chinese filler, …). Patching those
individual examples was explicitly *not* the goal.

The repair implements the intended boundary:

```
User text
  -> semantic interpreter (LLM) proposes an untrusted semantic candidate
  -> deterministic grounding (schema + enum + bounds + source-span accounting)
  -> governed Requirement
  -> Planner / Analytics
```

The deterministic layer still validates authority, schema, Measures, entities, time,
population, clause consumption and execution conformance. The LLM still cannot create SQL,
trusted Measure identity, authoritative filters or authoritative Requirement values by itself.
A candidate only becomes authoritative when its meaning is schema-grounded **and** its cited
user spans account for the deterministic residue, leaving no unexplained semantic token.

Result (live DeepSeek, 48 fresh paraphrase/typo cases, seeded):

| metric | value |
|---|---|
| correct semantic interpretation | 41 / 48 = **85.4 %** |
| correct semantics, deterministic parser only (baseline) | 34 / 48 = 70.8 % |
| semantic-candidate lift | **+14.6 points** |
| correct + analytically complete (execution rate) | 28 / 48 = 58.3 % |
| safely unresolved | 7 / 48 = 14.6 % |
| **wrong semantic COMPLETE** | **0** |
| provider errors | 0 |

Safety invariants are intact: the M2.12R4 independent attacker reports `failures: 0` over
514 attacks, and the full suite is `3151 passed, 31 skipped`.

## 1. Scope and method

The audit inspected production semantic and analytics code, not only `conformance.py`.
Files read in full or in relevant part:

| file | responsibility |
|---|---|
| `app/semantic/interpreter.py` | open-world semantic interpretation (LLM) |
| `app/semantic/goals.py` | RequirementBuilder; completion, role, measure, mention shaping |
| `app/semantic/relations.py` | canonical relation semantics + cue vocabularies |
| `app/semantic/decomposition.py` | atomic propositions; task-language stripping |
| `app/semantic/entities.py` | contextual entity references; pronouns/bigrams |
| `app/semantic/relation_attribution.py` | directed cause/effect clause matching |
| `app/semantic/temporal.py` | relative/absolute temporal-scope resolution |
| `app/capabilities/analytics/conformance.py` | analytical obligations, role, clause coverage, receipts |
| `app/capabilities/analytics/authority.py` | Requirement authority resolution |
| `app/capabilities/analytics/derivations.py` | deterministic semantic transformation proofs |
| `app/capabilities/analytics/measures.py` | governed Measure definitions + aliases |
| `app/capabilities/analytics/catalog.py` | real source schema / enums / populations |
| `app/capabilities/analytics/predicates.py` | finite predicate validation |
| `app/web/ranking.py`, `span_selector.py`, `query_plan.py` | relation-cue consumers |
| `app/web/language.py` | language/community markers |

Methods:

* string/regex/word-list scanning (`grep`, AST-free inventory);
* classification of every relevant rule as A/B/C/D (below);
* identification of rules whose lexical match controls answer-changing semantics;
* incremental refactor of the highest-risk instance;
* generated paraphrase + typo populations validated live against DeepSeek.

## 2. Classification scheme

* **A — legitimate closed-world domain rule.** BATTER/PITCHER enum, EQ/IN/BETWEEN, governed
  game types, catalog fields, qualification bases, governed Measures, valid categorical
  values, canonical IDs. May remain deterministic.
* **B — stable terminology / alias mapping.** Known abbreviations, stable domain synonyms,
  multilingual canonical terminology. Belongs in Shared Knowledge, not scattered business
  logic.
* **C — open-ended natural-language interpretation.** Sentence wording, task verbs,
  paraphrases, clause order, user phrasing. Must not rely primarily on finite enumeration.
* **D — regression patch / benchmark-specific wording.** Added only because a prior test
  phrase failed. Highest-priority refactor candidate.

## 3. Hardcoding inventory

The inventory records representative rules per responsibility. `controls` = the semantic
decision the rule can change; `answer` = whether failure to match can change the numeric
answer.

| # | file / function | type | example pattern | semantic decision controlled | can change answer | class | disposition |
|---|---|---|---|---|---|---|---|
| 1 | `conformance.role_from_text` (911) | regex alternation | `batting(?!\s+average)\|hitting\|hitters?\|batters?`, `how did.+hit` | BATTER vs PITCHER subject | **yes** | C/D | replaced by semantic candidate; regex retained only as fallback |
| 2 | `conformance._consume_domain_filters` (1080) | regex alternation | `against\|versus\|vs\|facing … (left\|right\|L\|R)`, count words, CJK side tokens | row filters (`p_throws`, `stand`, `balls`, `strikes`) | **yes** | C | deterministic fallback + candidate path; handedness aliases centralized |
| 3 | `conformance.constraint_coverage` grouping (1252-1273) | regex dimensions dict | `split\|break down\|group by`, `按…分组`, `per … group` | `group_by` (split) | **yes** | C | candidate `splits` preferred; adapter retained |
| 4 | `conformance.constraint_coverage` ranking (1303-1309) | regex | `top\s+\d+\|leader\|most\|highest\|前\d+名`, tie-break | `limit`, `ordering`, `group_by` subject | **yes** | C/D | candidate `limit`/`ordering`; adapter fallback |
| 5 | `conformance.qualification` (115) | finite regex | `PA\|BBE\|PITCHES\|GAMES\|RESULTS` + `>=`/`至少` | qualification basis/minimum | yes | A/B | **retain** (governed bases) |
| 6 | `conformance._consume_inning_restriction` (1027) | regex + ordinal map | `eighth inning onward`, `第…局`, `inning N` | `inning` filter | **yes** | B/C | ordinal map centralized; candidate path preferred |
| 7 | `conformance._consume_unsupported_dimensions` (978) | regex | day/night, home/away, roof, weather, ballpark | UNSUPPORTED (blocks execution) | yes | A/B | **retain** (no governed field) |
| 8 | `conformance._NON_SEMANTIC_WORDS` (lexicon) | 144-word set | `how`, `did`, `totals`, `report`, `show`, `performance` | residue relevance → UNDERSTOOD/UNRESOLVED | **yes** | C | centralized; no longer the primary understanding mechanism |
| 9 | `conformance._DOMAIN_ACCOUNTED_PHRASES` (lexicon) | 7 multilingual phrase groups | `季後賽`, `表现`, `一共`, `那`, `呢` | residue accounting | yes | C | centralized; candidate `ignored_spans` supersedes for open-world wording |
| 10 | `conformance._SYNONYM_NORMALIZATION` (992) | phrase map | `southpaw/lefty/左投 → left-handed pitcher` | handedness filter | **yes** | B | moved to `lexicon.HANDEDNESS_PHRASES` |
| 11 | `goals._DEFINITION_MARKERS` (39) | phrase set | `什么是`, `what is`, `definition` | definitional vs analytical; suppresses measure | yes | C | candidate + interpretation; keep conservative fallback |
| 12 | `goals._EXPLANATION_MARKERS` (48) | phrase set | `为什么`, `why`, `background` | EXPLANATION completion vs statistical | yes | C | as above |
| 13 | `goals._VALUE_QUESTION_MARKERS` (57) | phrase set | `多少`, `how many`, `what was his` | whether a Measure is mandatory | yes | C | as above |
| 14 | `goals._bind_requirement_authority` (145) | regex | `postseason\|playoffs\|…` | regular-season domain default | yes | C/D | candidate/closure; default proof strengthened (R4 derivations) |
| 15 | `goals._requirements` population-role fullmatch (~180) | regex | `batters?\|hitters?\|pitchers?\|southpaws\|[左右]投手` | entity vs population | **yes** | C/D | retained as a safety guard (does not resolve identity); candidate role now authoritative |
| 16 | `relations.*_CUES` | ~380 phrase entries across 24 lists | `cause/why/原因`, `injury/交易`, `recent/最新` | RelationKind + routing + high-risk | yes | B/C | centralized in `relations.py`; relation nouns are finite domain concepts, sentence cues remain interpretive |
| 17 | `temporal._FIXED_RELATIVE_DAYS` / `_CLASS_RELATIVE_TERMS` | phrase sets | `recent`, `最近`, `this week` | resolved temporal window | yes | B/C | retain; deterministic, inspectable, one place |
| 18 | `entities._PRONOUN_SURFACES` | phrase set | `他`, `this player` | contextual antecedent | yes | B | retain (closed grammatical class) |
| 19 | `relation_attribution._PATTERNS` | regex | `caused|led to|resulted in|due to` | directed cause/effect support | no (evidence gate) | A/B | retain (finite causal syntax) |
| 20 | `decomposition._TASK_LEAD` | phrase set | `explain`, `分析`, `查找` | proposition factual core | no (verifier prep) | C | retain; not an execution decision |
| 21 | `measures._MEASURE_LIST` aliases | phrase map | `本垒打/home runs/hr` | Measure identity | **yes** | B | canonical Shared Knowledge; retain |
| 22 | `catalog.*` enums | finite enums | `EVENT_ENUM`, `PITCH_TYPE_ENUM`, `POPULATIONS` | valid values / populations | **yes** | A | **retain** |
| 23 | `web/language.py` community markers | phrase sets | `日本媒体`, `korean media` | research language expansion | no | B/C | retain (research, not factual) |

## 4. Highest-risk lexical decisions

These decide the numeric answer and were the priority targets:

1. **BATTER vs PITCHER** (`role_from_text`). Controlled by a growing phrase list and special
   cases such as `how did … hit`. This is the single highest-risk decision.
2. **Row-population filters** (`_consume_domain_filters`): handedness, count, pitch type,
   inning.
3. **Grouping/splits**: every sentence-level way to request `p_throws`/`stand`/`pitch_type`.
4. **Ranking/limit/ordering**.
5. **Semantic limit** and **qualification**.
6. **UNDERSTOOD vs TEMPORARILY_UNRESOLVED** via the task-word list.
7. **Entity vs population**.
8. **Temporal scope** (year/relative window).

## 5. Regex/keyword counts by semantic responsibility

Measured before centralization (2026-10-05):

| responsibility | location | count |
|---|---|---|
| relation cue phrases | `app/semantic/relations.py` | 24 lists, ~380 entries |
| task/presentation words | `conformance._NON_SEMANTIC_WORDS` → `lexicon` | 144 |
| role words | `conformance._ROLE_WORDS` → `lexicon` | 15 |
| multilingual accounted phrases | `conformance._DOMAIN_ACCOUNTED_PHRASES` → `lexicon` | 7 groups |
| inning ordinals | `conformance._INNING_WORDS` → `lexicon` | 20 |
| count words | anonymous dict → `lexicon.COUNT_WORDS` | 10 |
| regex call sites in `conformance.py` | analytics adapter | 38 |
| regex call sites in `relations.py` | relation semantics | 1 |
| regex call sites in `goals.py` | RequirementBuilder | 5 |
| regex call sites in `temporal.py` | temporal resolution | 3 |

`conformance.py` (1 402 lines) carried the highest density of answer-changing lexical rules.

## 6. Regression-specific patches (class D)

Named because they exist only for a prior wording, not because they are individually wrong:

* `role_from_text`: `how did.+(?<![A-Za-z])hit(?![A-Za-z])` → BATTER. Removed from the primary
  role decision; the interpreter candidate now supplies open-ended role reading.
* `role_from_text`: `batting average|打击率 → BATTER`, `被打击率 → PITCHER`. A lexical
  tie-break for a specific metric surface.
* `conformance.constraint_coverage`: `consume(r'(打者|投手)中', …)` role tie-break.
* `goals._bind_analytical_source_clauses`: `task_start` regex
  (`count|report|show|…|统计|說明`) used to decide independent clause partitions.
* `goals._bind_requirement_authority`: the postseason exclusion regex.
* `goals._measure_requirements`: inning-span masking to stop `IP` (innings pitched) being
  inferred from an inning *restriction*.

These are retained where they are safe fallbacks, but they are no longer the mechanism for
understanding novel wording.

## 7. Retained legitimate deterministic rules

Explicitly kept (classification A), because they are closed-world and governed:

* `EntityConstraint.role ∈ {BATTER, PITCHER}`;
* `qualification` bases `{PA, BBE, PITCHES, GAMES, RESULTS}`;
* catalog field operator sets (`FILTER`/`GROUP_BY`/`AGG`/`DERIVE`);
* `EVENT_ENUM`, `PITCH_TYPE_ENUM`, `POPULATIONS`, `stand`/`p_throws ∈ {L,R}` and bounds;
* governed Measure definitions and their structural IR verification;
* `_UNSUPPORTED_DIMENSIONS` (no governed field ⇒ must remain UNSUPPORTED);
* `_mandatory_clauses` conjunction splitter preserving quotes, `IN`, `BETWEEN`;
* authority, conformance, receipt and Judge verification.

The goal was never “regex bad”. Deterministic finite syntax (dates, `IN (...)` lists,
operators, `BETWEEN`) and closed-world enums are appropriate and stay.

## 8. Terminology centralized

New module `app/semantic/lexicon.py` (Shared Knowledge / classification B):

* `ROLE_WORDS`, `HANDEDNESS_PHRASES`, `SIDE_VALUES`;
* `PITCH_TYPE_NAMES` (Statcast enum synonyms);
* `POPULATION_TERMS`;
* `INNING_WORDS`, `COUNT_WORDS`;
* `NON_SEMANTIC_WORDS`, `DOMAIN_ACCOUNTED_PHRASES` (classification C, centralized — **not**
  relocated as a “solution”, only de-duplicated).

`conformance.py` now imports these names; behaviour is identical and the full suite passes.
This removes copy-paste drift but does not by itself make open-world language robust — that is
the job of the semantic-candidate boundary below.

## 9. Semantic-interpreter boundary changes

New module `app/semantic/analytical_candidate.py`:

* **Semantic dimension registry** — bridges stable semantic concepts to governed fields:
  `opposing_pitcher_hand → p_throws`, `batter_hand → stand`, `pitch_type`, `balls`,
  `strikes`, `inning`, `zone`, `game_type`; plus grouping and role.
* `ground_candidate(candidate, source_text)` — maps a dimension/value (including
  `left/lefty/southpaw/LHP/左 → L`, `slider → SL`), validates the value against the real
  catalog enum/bounds/operators, re-validates the whole obligation dict through the governed
  adapter, and records `source_spans`/`ignored_spans`.
* `candidate_proves(parent_text, candidate, parents)` — the conservation proof: the candidate
  must be proven against an authoritative **ANALYSIS/EVIDENCE** parent (never a season/scope
  value), may only cover text the deterministic parser left unresolved (never an
  already-consumed span), may not cover an UNSUPPORTED dimension (e.g. roof), and after its
  spans + interpreter-declared `ignored_spans` + bound sibling terms (entity/season/Measure/
  population) are removed, no semantically relevant residue may remain.

`app/semantic/interpreter.py` SYSTEM_PROMPT now asks the interpreter for
`analytical_candidates` in that semantic-dimension shape, with exact `source_text` spans and
`ignored_spans` for task wording, and explicit rules never to invent a restriction, split,
limit, ordering, qualification or number.

RequirementBuilder (`goals.py`):

* `analytical` constraints are grounded; a candidate whose obligations the finite adapter
  already produces is dropped as redundant;
* accepted candidates become `source="inferred"` Constraints with
  `derivation_kind="governed_semantic_candidate"`, bound to **all** bound user
  Requirements;
* `bound_source_clause` forces the proof to run against the **original user wording**, never
  the interpreter's paraphrase.

Authority (`authority.py` + `derivations.py`):

* new governed derivation `governed_semantic_candidate`;
* `transformation_valid` proves span conservation rather than trusting proof prose.

Conformance (`conformance.py`):

* `bind_obligations` adds candidate `source_spans`/`ignored_spans` to the source-review
  accounted terms (longest surface first, articles normalised);
* an authoritative candidate role takes precedence over the brittle text role cue, while
  entity-resolution roles still conflict-check.

The LLM candidate remains untrusted: it cannot create SQL, choose a source, assert a Measure
identity, forge an entity ID, or pass without schema grounding and span conservation.

## 10. Typo strategy and safety

Typo tolerance is interpretive, not a fuzzy schema lookup:

1. the interpreter sees the noisy text and proposes a **semantic dimension** (e.g.
   `opposing_pitcher_hand = left` from `lfeties`, `aginst leftes`, `对左投手`);
2. `ground_candidate` grounds the dimension against the governed field and enum;
3. `candidate_proves` requires the cited span to be literal user text and to cover the
   deterministic residue.

No production code fuzzy-matches a string to the nearest schema field. Tested categories:
character deletion, adjacent transposition, duplicate character, capitalization, whitespace
insertion/removal, punctuation removal, CJK character substitution.

* **High-confidence recoverable typo** (unique governed mapping, span covers residue) →
  normal validation and execution.
* **Ambiguous typo** (multiple material meanings or unexplained residue) → remains
  `TEMPORARILY_UNRESOLVED`.
* **Unknown concept** → unresolved, never fuzzy-bound to an unrelated field.

Safety example preserved: candidates cannot erase `under a closed roof` (governed
UNSUPPORTED context) or any already-parsed dimension. The attacker regression
`test_candidate_cannot_erase_unsupported_context` and
`test_candidate_cannot_cover_already_parsed_meaning` lock this in.

## 11. Paraphrase testing methodology

Offline (`tests/test_nl_semantic_candidate_m212r4.py`, 32 tests):

* semantic-dimension grounding table across synonyms/abbreviations/Chinese;
* unsupported candidate rejection (unknown field, bad enum, out-of-range, wrong role, limit);
* span-accounting safety (scope-value parent, unsupported context, parsed-span overlap,
  unknown residue, unfounded role);
* paraphrase-family convergence (formal, casual, Chinese, English, mixed, abbreviation,
  reordered clauses, typo) to one governed contract;
* metamorphic generation: synonym/order/abbreviation changes hold the contract constant;
* a deterministic-path control proving no regression without a candidate.

Live (`tools/m212r4_nl_robustness.py`): the oracle semantic intent is defined **before**
text generation; mutations are generated from a seeded RNG and never listed in production
code. The tool computes both the semantic-candidate result and the deterministic-only
baseline for the same cases.

## 12. Live DeepSeek testing

Environment: `deepseek-flash`, endpoint `https://api.deepseek.com`, credential present. The
key is never printed or persisted.

Command:

```
python -m tools.m212r4_nl_robustness docs/audit/m212r4/nl_robustness_live \
    --seed 555000 --per-intent 2 --typos 2
```

Evidence: `docs/audit/m212r4/nl_robustness_live/corpus.json` and `live_results.json`.
Populations cover 8 intent families × (2 paraphrases + 2 typos) = 48 fresh cases:
opposing-pitcher-hand left/right, role PITCHER, pitch-type SL, 2-strike count, 3-ball count,
inning 7, and pitcher-hand split.

## 13. Semantic correctness metrics (live, 48 cases)

| outcome | count |
|---|---|
| CORRECT_COMPLETE | 28 |
| CORRECT_INCOMPLETE (semantics correct, other clause residue) | 13 |
| SAFE_UNRESOLVED | 7 |
| WRONG_SEMANTIC (incomplete) | 0 |
| **WRONG_COMPLETE** | **0** |
| PROVIDER_ERROR | 0 |

* semantic intent recovery = (28 + 13) / 48 = **85.4 %**
* deterministic-parser-only baseline = 34 / 48 = 70.8 %
* candidate lift = **+14.6 points**

## 14. Safe unresolved / utility metrics (live, 48 cases)

* safely unresolved = 7 / 48 = 14.6 %
* wrongly executed / wrong semantic COMPLETE = **0**
* natural-language analytical execution rate (correct + analytically complete) = 28 / 48 =
  58.3 %; the residual 13 `CORRECT_INCOMPLETE` are semantics recovered but a task word the
  interpreter did not declare as `ignored_spans` kept the clause unresolved. This is a
  conservative utility gap, not a safety gap.

## 15. Remaining hardcoded language dependencies

1. `conformance._NON_SEMANTIC_WORDS` (144 words) still contains natural-language task words.
   A large list is a warning sign (§15); it is now centralized and is no longer the primary
   understanding mechanism, but it still decides residue relevance when no candidate exists.
2. `goals._DEFINITION/EXPLANATION/VALUE_QUESTION_MARKERS` still decide completion for
   questions the interpreter does not structure.
3. `role_from_text` regexes remain as the offline/fallback role path.
4. Relation-cue vocabularies in `relations.py` remain finite; unknown paraphrases fall back
   to GENERAL.
5. `_consume_domain_filters`/grouping/ranking regexes remain the fallback parser.
6. Temporal relative phrases remain a finite list.

None of these are the *primary* path when the interpreter is configured; they are bounded
fallbacks. They are the recommended targets for the next milestone.

## 16. Recommendations for M2.12R4 / next milestone

1. Make the semantic-candidate path the default online path and measure candidate coverage;
   keep the deterministic parser as the offline/replay path.
2. Move remaining completion markers and relation cues behind the same candidate boundary,
   or classify them explicitly as finite domain concepts.
3. Have the interpreter emit complete `ignored_spans` (task wording) so `CORRECT_INCOMPLETE`
   cases become complete rather than safely unresolved — without touching the task-word list.
4. Delete the class-D regressions once the candidate path is proven in production:
   `how did … hit`, `(打者|投手)中`, `task_start`, and the postseason exclusion regex.
5. Keep §7 deterministic rules and the §9 conservation proof frozen; do not relax the
   “candidate may not cover consumed/unsupported text” rule.
6. Extend the live corpus with more role, grouping, qualification and ranking families, and
   continue to require `wrong semantic COMPLETE = 0`.

## 17. Stop-question answers

1. **Which production semantics depend on finite user-word enumeration?** Role
   (BATTER/PITCHER), filters (handedness/count/pitch type/inning), grouping/splits,
   ranking/limit, qualification phrasing, completion (definition/explanation/value),
   relation routing, temporal relatives and residue relevance.
2. **Which are legitimate closed-world domain rules?** Enums, catalog fields/operators,
   qualification bases, governed Measures/populations, canonical IDs, unsupported dimensions,
   conjunction/operator syntax.
3. **Which are open-ended and should not be hardcoded?** Role paraphrases, filter/split
   phrasing, ranking phrasing, task verbs and clause order, definition/explanation wording.
4. **Are BATTER/PITCHER decisions dependent on growing phrase lists?** They were. They now
   primarily come from the grounded semantic candidate; the phrase path is a bounded
   fallback and no longer receives new benchmark-specific entries.
5. **Are grouping decisions dependent on sentence-level regex enumeration?** The fallback is;
   the primary path is a semantic `group_by` candidate validated against the catalog.
6. **Are task-language/residue decisions dependent on large word lists?** Yes, when no
   candidate exists; the candidate’s `source_spans`/`ignored_spans` now supersede the list for
   open-world wording, and the list is centralized and frozen.
7. **Can minor typos cause valid supported requests to become unresolved?** Sometimes, yes —
   14.6 % stayed safely unresolved (mostly task words not declared as ignored spans, and
   ambiguous mutations).
8. **Can typos cause a wrong semantic interpretation?** Not a wrong COMPLETE in the live
   population (`0`). Wrong-but-incomplete is still kept unresolved rather than executed.
9. **Can new paraphrases be understood without modifying Python vocabulary?** Yes — the live
   run recovered meaning for wording absent from the Python vocabulary (e.g. `facing
   portsiders`, `a pitcher throwing from the left side`, `lfeties`).
10. **Is stable baseball terminology centralized?** Yes: `app/semantic/lexicon.py` (roles,
    handedness, pitch types, populations, ordinals, counts) and `measures.py` aliases.
11. **Does LLM interpretation propose semantics while deterministic code validates them?**
    Yes: schema/enum/bounds grounding, authority, span conservation, population/Measure/
    entity/temporal validation, conformance and receipts all remain deterministic.
12. **Are clause-consumption safety guarantees preserved?** Yes. Candidates may not cover
    already-consumed or UNSUPPORTED text, must cite literal spans, and must leave no
    unexplained semantic token; `UNDERSTOOD`/`TEMPORARILY_UNRESOLVED`/`UNSUPPORTED` accounting
    is unchanged.
13. **Do fresh typo/paraphrase tests produce wrong semantic COMPLETE?** No — `0`.
14. **Can fresh unfamiliar wording reach Local Analytics execution?** Yes — 58.3 % in the live
    population were correct **and** analytically complete; two live families
    (`opposing_pitcher_left/right`) reached 5/6 each.
15. **Has production moved measurably toward open-world language / closed-world execution?**
    Yes — semantic recovery rose from 70.8 % (deterministic only) to 85.4 % with the
    candidate boundary, with `wrong semantic COMPLETE = 0`.

## 18. Git status

Branch: `codex/phase5b-final-repair`
Base commit: `66f1f5712b87c38e69be9a143af597d44f10d473`
Working tree: M2.12R4 authority/conformance work plus this audit’s changes, uncommitted.

Audit/refactor files:

* `app/semantic/lexicon.py` (new)
* `app/semantic/analytical_candidate.py` (new)
* `app/semantic/interpreter.py` (candidate prompt + parsing)
* `app/semantic/goals.py` (candidate Requirement + clause binding + role precedence)
* `app/capabilities/analytics/conformance.py` (lexicon, source-span accounting, candidate role)
* `app/capabilities/analytics/authority.py`, `derivations.py` (`governed_semantic_candidate`)
* `tests/test_nl_semantic_candidate_m212r4.py` (new, 32 tests)
* `tools/m212r4_nl_robustness.py` (new live harness)
* `docs/audit/m212r4/nl_robustness_live/` (live evidence)
* `docs/audit/NATURAL_LANGUAGE_HARDCODING_AUDIT.md` (this file)
* `docs/audit/M212R4_IMPLEMENTATION_REPORT.md` (references this audit)

Verification:

* `python -m pytest -q` → `3151 passed, 31 skipped`
* `python -m tools.m212r4_attacker … --seed 419331` → `514 attacks, failures: 0`
* live DeepSeek: `wrong semantic COMPLETE = 0`, semantic recovery 85.4 %.
