# ADR 0023 — Post-Composition Status/Answer Consistency

Status: accepted (Phase 5B, 2026-09-25; amended by ADR 0025, 2026-09-27)

> **Closure amendment (ADR 0025).** The consistency check no longer calls an LLM. It is a
> structured comparison of the rendered answer against the accepted claims attached to each
> mandatory satisfied proposition. A missing mandatory result triggers a bounded
> deterministic repair built from those claims; only if repair cannot express the result is
> `COMPLETE -> LIMITED` recorded. Missing *optional* material never downgrades, and an
> unrelated limitation does not count as a retraction of an expressed result.

## Context

The F3 failure had two halves: the Judge reported `COMPLETE`, and the Composer then
produced prose saying the mandatory result was unavailable. The Composer must not create a
new factual authority: if it retracts a verified mandatory obligation, the recorded status
must not remain `COMPLETE`.

## Decision

After the Composer returns, `app/verification/consistency.py` checks the rendered answer
against every mandatory, satisfied prose proposition (`FACT`, `EVENT`, `COMPARISON`,
`EXPLANATION`, `GROUNDED`; `IDENTITY`/`SCOPE` are preconditions the answer expresses
implicitly and are excluded).

- The signal is **structured**: each mandatory satisfied proposition's accepted claims are
  compared with the answer. Numeric results are language-independent and must be present;
  a cross-script answer with no comparable tokens is reported `UNCERTAIN`, never a
  manufactured retraction.
- A missing mandatory result is first repaired with a bounded, deterministic block of the
  already-verified claims. If the repaired answer expresses the result, the status is
  preserved; otherwise the run is downgraded.
- The check never calls an LLM and never decides evidential support; it is not a second
  Evidence Verifier.

If a mandatory proposition was not communicated, the runtime first attempts the bounded
repair and only then, if necessary, downgrades (`COMPLETE -> LIMITED`), recording the
transition in `StatusConsistency` (original/final status, affected proposition ids, method,
rationale) and surfacing it as a limitation. It is deliberately not a bare keyword scan for
"无法"/"不能"/"未找到".

## Consequences

- `COMPLETE` now means "all mandatory propositions are covered **and** the final answer
  communicates the result".
- The correction is auditable: the mismatch, its method and the affected propositions are
  persisted in the projection and the diagnostic record.
- A confident-sounding answer can never promote a Goal to `COMPLETE`; only proposition
  coverage can.
