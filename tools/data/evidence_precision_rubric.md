# Evidence Precision (EP) adjudication rubric — Web Research M1

Status: M1. Applies to the frozen task set in `tools/data/research_tasks.json`.

Evidence Precision answers a single question for every **admitted `SUPPORTED` finding**:

> Does the cited passage genuinely establish the proposition, under the proposition's
> subject, metric/relation and scope?

It is **not** a style or fluency judgement, and it is not affected by answer wording.

## Definitions

- **Admitted `SUPPORTED` finding**: an `EvidenceFinding` with `status == "SUPPORTED"` and
  `admissible == True`, whose `span_id` resolves to a persisted `EvidenceSpan` of an `OK`
  document and whose offsets replay (`body[start:end] == span.text`).
- **EP numerator**: admitted `SUPPORTED` findings adjudicated `genuine = true`.
- **EP denominator**: all admitted `SUPPORTED` findings, including unadjudicated ones.
  Unadjudicated findings are counted in the denominator and reported separately
  (`evidence_precision_unadjudicated`) so EP can never be inflated by silence.

`1 - EP` is the false-support rate (admitted supported findings that do not genuinely
establish the proposition).

## Adjudication procedure

For each admitted `SUPPORTED` finding, re-read the span text only (never the search snippet
or the page title) and apply the checks in order. Any failure makes the finding **not
genuine** (`genuine = false`).

1. **Subject.** The passage itself names the proposition subject (or a recorded alias).
   A passage that only names the subject in the title does not qualify.
2. **Relation / metric.** The passage asserts the requested relation or metric. A passage
   that mentions the subject and an unrelated fact does not qualify.
3. **Scope.** The passage establishes the requested time scope and population (a different
   year or population is a hard mismatch; a missing scope is not a match).
4. **No contradiction.** The passage does not deny, hedge or reverse the proposition.
5. **Attribution.** When several entities or values appear, the value belongs to the
   proposition subject, not to a neighbouring entity.

Structured facts (for example an exact numeric value in one sentence) are additionally
re-checked deterministically: the value must appear in the passage bound to the requested
metric and scope. The deterministic re-check is authoritative over a subjective reading.

## Recording the adjudication

Adjudications are stored as a JSON map of `finding_id -> true|false` (committed alongside
the run that produced the finding). The harness applies this map in both offline and live
modes; a run may also carry an explicit `genuine` label for offline fixtures.

## Honesty rules

- A finding may be labelled `genuine = true` only if the passage establishes the obligation;
  a plausible or on-topic passage is not enough (`INSUFFICIENT`, not `SUPPORTED`).
- ACR and EP are never traded against each other silently. Raising ACR by admitting weaker
  support lowers EP and is reported as a regression.
- Live runs depend on the date and external source availability; every report records the
  mode, build label and generation time.
