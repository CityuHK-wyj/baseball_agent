# v0.2 anti-shortcut and generalization audit

Decision: `GENERALIZATION_AUDIT_BLOCKED`

Reviewed branch: `codex/v0.2-anti-shortcut-audit`, created from exact Pi SHA
`7c3b6bdd426147e5cc7780900678bede7def913c`. Base: `68c67507af032721f32b4fe380d48c3df43aedc4`.
Released main `35b47c4174e98460ebdbcd2ace46adfba36225c4` was not modified. The review
diff is the 19-file, 2,601-line v0.2 change set. Working-tree additions are only this
report and `reproduce.py` in this directory.

The full suite passed (`python3 -m unittest discover -s tests -q`: 565 tests, OK), but the
new tests are example-shaped and do not establish generalization.

## Findings

### P1-01: unrelated evidence becomes COMPLETE

`app/agent/agent.py:310-318` returns `COMPLETE` whenever any evidence exists, except for
some recovery/caveat cases. It does not compare evidence with the requested entity, time,
team, metric, population, or all compared entities. `app/agent/agent.py:274-281` can add
raw recovery web evidence before this test. A valid but irrelevant artifact can therefore
look like a solved objective.

Reproduction: `PYTHONPATH=. python3 docs/reviews/v02-anti-shortcut-audit/reproduce.py`.
Observed: `status_shortcut COMPLETE` for an unrelated web item.

### P1-02: date-aware plans execute season data

`app/agent/agent.py:199-213` always calls `season_evidence`; it never calls
`range_evidence`. The plan can contain dates, but the batting executor ignores them. A
“last 30 days” request can receive a current-season line without a limitation. The
reproduction records `season_calls: [2026]` and `range_calls: []`.

### P1-03: team population is city matching

`app/tools/batting.py:158-172` filters BRef city values. `New York Yankees` returns both
Yankees and Mets; `Chicago Cubs` returns both Cubs and White Sox. The caveat at
`:225-234` does not correct the rows. Dodgers/Angels has the same structural risk. This
is a material population error independent of the dogfooded Dodgers query.

### P1-04: explicit unsupported constraints are silently weakened

`app/agent/local_analytics.py:68-94` defaults invalid aggregation/direction/limit, ignores
unsupported pitch and location values, and filters invalid game types out. `_time_range`
(`:99`, `:190-197`) turns malformed dates into no time range, which selects an unrestricted
source. Some cases get a caveat, but none reliably blocks a materially different query.

### P2-01: known phrase in deterministic fallback

`app/agent/cognition.py:308-312` maps the exact Chinese vocabulary `高区`, `高區`, and
`快速球` to exit velocity. The same fallback does not recognize an equivalent unseen
English formulation. Its resolved-entity branch (`:305-323`) generally turns questions
into season batting and a generic contact comparison. This is direct evaluation-shaped
phrase handling plus a narrow fallback.

### P2-02: snippets are accepted as grounded web evidence

`app/tools/web_research.py:229-245` emits a WEB EvidenceItem for every result and uses its
snippet when page reading returns empty. There is no claim-level extraction, source-quality
check, conflict/freshness check, or requirement match. The reproduction returns accepted
snippet-only evidence from an unrelated result. The answer prompt's “use only evidence”
instruction does not make that evidence relevant.

### P2-03: local analytics is a predefined lookup

The planner catalog/local runner restrict local work to two metrics, four aggregations,
three pitch families, fixed locations/populations, and one ranking shape. The local hint is
compiled as one RankingConstraint; there is no general grouping, multiple aggregate,
conditional ratio, period difference, threshold-rate, or join operation. The runtime also
passes only the first batting metric (`app/agent/agent.py:207-212`). This is a safe narrow
slice, not general composition over available raw fields.

### P2-04: fallback silently changes questions

`app/llm-first` fallback paths in `app/agent/cognition.py:126-166` reduce provider failure
to cue dictionaries and entity substring matches. Dates, team identity, research strategy,
derived metrics, and most follow-up semantics are lost while season evidence can still be
returned. This is a scope-changing fallback, not a bounded LIMITED answer.

### P2-05: follow-up context is raw text, not reusable semantics

`app/agent/agent.py:296-302` stores messages and clarification text, but the fallback has
no mechanism for “the prior year,” “postseason instead,” “only left-handed,” “lower the
threshold to 10,” or “use maximum EV” to update the previous objective. The known test only
checks that `那去年呢？` appears in history.

### P3-01: builder-friendly tests and documentation

The new tests directly use the supplied `太鼓达人`, `高区快速球`, Ohtani, Judge, DFA,
Dodgers-era city, 2025, and exact recent-30-day examples. Assertions mostly check that a
status is nonfailed, a name exists, evidence exists, or history contains text. They do not
check scope, provenance, compared-entity completeness, or answer support. README/usage
also repeat dogfooding queries; that is legitimate documentation, but those queries cannot
serve as independent acceptance evidence.

## Required area verdicts

Literal leakage: no player-specific production answer constants or player branch were
found. Exact examples occur in docs/tests. The production fallback's Chinese high-fastball
branch is suspicious. Coverage years are legitimate configuration, but user windows are
not consistently honored.

Prompt leakage: the planner prompt is broadly written and contains only the generic DFA
example, not a full few-shot acceptance set. Live/test validation is contaminated by the
new tests' exact known examples and lacks independent substitutions/paraphrases.

Hard-coded answers: none found in v0.2 production code; values are retrieved when paths
are selected. Wrong-scope evidence remains a serious failure.

Runtime special cases: confirmed at `cognition.py:308-312`; entity recognition is seeded
dictionary substring scanning at `agent.py:284-294`.

Synthetic/demo isolation: normal `ask`/`chat` uses `build_agent` and does not inject
SyntheticDataTool. CLI `--demo`, `--empty`, and `--legacy` explicitly route to the legacy
path (`app/cli.py:148-153`). This passes for normal live commands, with the operational
caveat that legacy synthetic modes remain in the repository and must stay explicit.

Team population: FAIL, P1-03. Web generalization: transport/query handling is generic,
but unknown-language/entity expansion and evidence quality are unproven; snippets are too
weak. CandidateKnowledge governance: PASS for pending status and explicit administrative
promotion (`app/agent/agent.py:326-341`, `app/knowledge/candidates.py:38-73`); language is
stored as `und`, so contextual preservation is incomplete.

Success-state/evidence relevance: FAIL, P1-01. Fallback: FAIL, P2-04. Response bluffing:
FAIL, P2-02 plus P1-01; `LIMITED` is not enforced in composer language. SQL/schema safety:
the closed compiler/field mapping prevents arbitrary physical fields, a genuine reusable
mechanism, but the adapter weakens invalid semantic values before compilation. Analytical
expressiveness and derived calculations: FAIL, P2-03; only fixed BB%/K% are derived.
Follow-ups: basic history passes, semantic fallback generality fails, P2-05. Clarification:
first-class waiting state passes, but detection is model-dependent and repeat suppression is
a 40-character substring check (`agent.py:304-307`). Broad exceptions normalize outages but
also turn failures into weak evidence/recovery paths.

## Independent holdout set

Created after inspection and stored only here; none was added to prompts/product code.

| Case | Classification | Basis |
|---|---|---|
| Last 30 days, two unseen hitters, compare OPS | `FAIL_OVERFIT` | season path, no range call |
| 2025 New York Yankees hitters | `FAIL_UNSAFE` | Yankees and Mets mixed |
| 2025 Chicago Cubs hitters | `FAIL_UNSAFE` | Cubs and White Sox mixed |
| unfamiliar Spanish nickname for a prospect | `PASS_LIMITED` | raw web route exists; grounding unproven |
| unfamiliar Japanese nickname | `PASS_LIMITED` | same; candidate stays pending |
| two unseen pitchers, postseason WHIP | `FAIL_CAPABILITY` | season-only pitching path |
| threshold-rate difference across two periods | `FAIL_CAPABILITY` | no operation representation |
| breaking-ball velocity grouped by batter/year | `FAIL_CAPABILITY` | no grouping/multi-period contract |
| invalid plausible physical field | `PASS_GENERAL` at SQL guard | closed field boundary |
| valid raw field outside metric catalog | `FAIL_CAPABILITY` | artificial restriction |
| prior year, postseason, left-handed, threshold follow-ups | `FAIL_CAPABILITY` under fallback | no semantic updates |
| relevant title but failed page fetch | `FAIL_UNSUPPORTED` | snippet becomes evidence |
| valid unrelated result for precise question | `FAIL_UNSAFE` | status becomes COMPLETE |
| provider timeout on unseen date/team query | `FAIL_OVERFIT` | fallback changes scope |

Failures cluster around temporal scope, team identity, unseen metrics/calculations,
follow-ups, and evidence relevance. They are architecture failures and do not depend on
the named dogfooding entities.

## Commands and exact review tree

```text
git show -s --format='%H %D %s' 7c3b6bdd426147e5cc7780900678bede7def913c
git diff --stat 68c67507af032721f32b4fe380d48c3df43aedc4 7c3b6bdd426147e5cc7780900678bede7def913c
python3 -m unittest discover -s tests -q
PYTHONPATH=. python3 docs/reviews/v02-anti-shortcut-audit/reproduce.py
git diff --check 68c67507af032721f32b4fe380d48c3df43aedc4 7c3b6bdd426147e5cc7780900678bede7def913c
```

Observed: all 565 tests passed; the reproduction printed the four failures described
above. The review commit/tree under audit is the exact Pi commit; report/reproduction are
uncommitted audit artifacts on the audit branch.

## Final answer and recommendation

Severity summary: P0 none found; P1 findings P1-01 through P1-04; P2 findings P2-01
through P2-05; P3 finding P3-01. No destructive or security/data-loss behavior was
demonstrated.

If all developer examples disappeared tomorrow, reusable mechanisms would be the closed
SQL/schema boundary, explicit candidate status and admin promotion, bounded HTTP/SSRF
transport, basic conversation storage, and source adapters. The parts working mainly
because the builder knew the examples are the fallback's phrase-to-analysis mapping, the
weak example-shaped tests, season-only batting behind date-aware planning, city-based team
filtering, and evidence/status logic that treats plausible retrieval as a solved objective.

Do not merge or release. Review this diagnosis first. The next implementation round should
establish requirement-level evidence matching and scope-aware status, preserve every
explicit constraint, use authoritative team identity, execute date ranges, and define a
general analytical operation contract before adding more examples or tests.
