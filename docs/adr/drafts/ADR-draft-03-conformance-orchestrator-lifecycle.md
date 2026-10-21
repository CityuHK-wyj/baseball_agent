# ADR-draft 03 — Composition Conformance and the Orchestrator Regeneration Lifecycle

Status: **accepted architectural decision; staged implementation in progress.**
Relates to ADR 0023 (status/answer consistency), ADR 0025 (verification boundary), WSA-06.

## Context

`conform_composition(result, package, assessment)` receives the governed `AnswerPackage`, the
Composer-provided `ComposerDraft`, and an optional LLM representation assessment. When the
assessment is missing (assessor outage) and there are no evidence origins, `_effective_epistemic`
falls back to the Composer's own `ComposedStatement.kind`, so self-labelled ANALYSIS/LIMITATION
prose survives (F-03). There is also no Composer regeneration loop; the Engine calls the
Composer once.

## Decision

1. Conformance is `Conformance(governed_snapshot, composer_draft)`:
   - `governed_snapshot` = `AnswerPackage`;
   - `composer_draft` = untrusted `ComposerDraft`.
2. Composer metadata (`kind`, `citations`, `supports`, `claim_markers`) is a **hint**, never
   authority. On a missing or partial representation assessment, Conformance **fails closed**:
   no unassessed free prose survives solely on any Composer label, including ANALYSIS and
   LIMITATION. Use governed Claims, QualifiedEvidence, real unresolved Requirements and
   limitations for deterministic fallback; omit free analysis if it cannot be safely assessed.
3. **LLM responsibility (Conformance):** open-language representation — faithful paraphrase,
   added factual clause, qualified→fact promotion, analysis-as-truth, invented comparison,
   dropped limitation, overstated causality.
4. **Deterministic responsibility:** snapshot identity, claim/source id existence, requirement
   linkage, citation identity, GoalStatus, schema validation, retry limits, immutability,
   routing violations to the Orchestrator.
5. **Orchestrator lifecycle:** after `Conformance`:
   - PASS → final answer;
   - presentation FAIL → Orchestrator requests Composer regeneration against the **same**
     governed snapshot, with the violation history (not the failed draft as authority);
   - representation violations report unauthorized additions, not world truth or reasons why
     evidence is missing. Orchestrator alone consults unresolved mandatory governed meaning
     to decide whether a reasonable acquisition path and remaining budgets justify replan.
   Conformance never calls the Planner and never re-Judges evidence.
6. Bounds: maximum two composition attempts (initial + one regeneration); deterministic
   rendering on exhaustion; Goal stays LIMITED when a mandatory proposition is uncovered;
   at most one post-Judge Planner replan; all attempts recorded.

## Invariants

- Composer and Conformance consume the same trusted governed state independently.
- Composer is the only layer allowed to synthesise/interpret; it never becomes a Claim source.
- Conformance does not silently rewrite Judge authority; a serious upstream inconsistency is
  escalated to the Orchestrator.

## Consequences

- F-03 closes by fail-closed policy.
- Regeneration is explicit, bounded and auditable via `OrchestratorDecision`.

## Non-goals

- No infinite Composer↔Conformance loop; record attempts, violations, decisions and fallback reason.
- No Judge 2.0: Conformance never determines whether an approved Claim should exist.
- No semantic regex/causal-cue/token-overlap expansion. Open-language representation checks
  belong primarily to the bounded semantic reviewer, not deterministic language policy.
- Status consistency and presentation repair cannot mutate Requirement status, Claim authority
  or GoalStatus. Governed inconsistency requires a new upstream pass through Orchestrator.
