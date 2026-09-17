# Examples

The default pipeline runs **offline** with a synthetic data source, so these examples
demonstrate the control flow (semantic → requirements → planning → assessment → state →
response) and the failure/limitation paths. With a configured source they would carry
real data; the synthetic artifacts are labelled `from synthetic`.

## Simple player performance

```bash
python3 -m app.cli ask "How did Aaron Judge perform at the plate?"
```

- Semantic layer resolves `Aaron Judge` to a canonical id (alias `Judge`, nickname
  `交通指挥员` also resolve).
- The decomposer produces a CORE performance requirement (contact quality) and an OPTIONAL
  historical baseline.
- Result: `COMPLETE` with accepted evidence and a limitation noting missing optional keys.

## Recent form / time window

```bash
python3 -m app.cli ask "How has Judge performed over the last 30 days?"
```

A time-window question. Partial coverage becomes a **soft signal** ("27/30 days") that the
Judge interprets contextually: acceptable for a trend, weak for an exact 30-day comparison.
Sample size is handled by `SampleAdequacyRule`, separately from eligibility
(`QualificationRule`).

## Complex Statcast filter

```bash
python3 -m app.cli ask "Two-strike high fastballs over 95 mph, highest exit velocity"
```

Each clause becomes a typed constraint (`strikes = 2`, `release_speed > 95`, a zone
constraint). The Planner stays semantic; physical column mapping happens in the Schema
Registry and Source Mapping, not in the plan.

## Qualified ranking

```bash
python3 -m app.cli ask "Top 5 qualified hitters by average exit velocity"
```

`qualification_rule = MLB_QUALIFIED` (who is eligible) is kept separate from
`sample_adequacy_rule` (is the sample enough to conclude). In-season qualification depends
on a `LeagueStateSnapshot`, and if official progress exceeds local ingestion coverage the
response carries a coverage limitation.

## Injury context

```bash
python3 -m app.cli ask "Was Judge injured last season?"
```

Produces an `INJURY` objective and an EXISTENCE evidence requirement. Nothing is asserted
without an evidence artifact; absence of evidence is reported as an unresolved item, not
as "no injury".

## Salary / value

```bash
python3 -m app.cli ask "Analyse Judge injury and salary value"
```

Two objectives (`INJURY`, `VALUE`) run as separate objectives, each with its own response
scoped to its own accepted evidence. A deterministic ratio of performance to salary is not
treated as "team value" — the response lists in-condition performance, sample and salary,
then a bounded judgement.

## Ambiguous nickname

```bash
python3 -m app.cli ask "How did Hernandez perform?"
```

With a dictionary containing two `Hernandez` entries, the resolver finds two equally
plausible candidates and returns a **clarification request with options and a
recommendation**. It does not silently pick the most famous one.

## Empty / no data

```bash
python3 -m app.cli ask "How did Judge perform?" --empty
```

Simulates zero rows. The artifact is rejected (hard/soft path), the requirement becomes
`UNSATISFIED`, planning stops with `NO_PROGRESS`, the objective is `FAILED`, and the
response contains **no** fabricated evidence and no accepted artifact.

## Persist, inspect and resume

```bash
python3 -m app.cli ask "How did Judge perform?" --persist
python3 -m app.cli inspect --run-id run-1
python3 -m app.cli resume  --run-id run-1
python3 -m app.cli metrics --run-id run-1
```

Checkpoints are written at `PLAN_ACCEPTED`, `ARTIFACT_ASSESSED`, `PLANNER_TERMINAL` and
`FINALIZATION`. Resume reports reusable artifacts and whether planning is terminal, so a
restart does not re-run expensive work or re-open a finished objective.
