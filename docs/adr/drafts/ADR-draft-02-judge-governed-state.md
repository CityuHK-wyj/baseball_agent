# ADR-draft 02 — Requirement-centric Judge and the Governed State Boundary

Status: **accepted architectural decision; staged implementation in progress.**
Relates to ADR 0021 (propositions), ADR 0022 (evidence verification), ADR 0025 (verification
responsibility boundary).

## Context

The Judge is already Requirement-centric but its output boundary is implicit: `RequirementState`
lives only inside `evaluate()`; `AnswerPackage` re-derives requirement coverage and can show a
compound parent as SATISFIED when one child is satisfied (F-06). There is no explicit governed
state shared independently by Composer and Composition Conformance.

## Decision

1. The Judge is the **single holistic Requirement-centric evidence governor** over all processed
   evidence. Approval is Requirement-local; completion is Goal-level.
2. The Judge output is the canonical governed state: `AnswerProjection`.
   - add `requirement_assessments: list[RequirementAssessment]`;
   - keep `propositions` coverage as the sole `GoalStatus` authority;
   - keep `claims`, `qualified_evidence`, `limitations`, `source_inspections`,
     `evidence_origins`;
   - freeze the projection immediately when `Judge.evaluate()` returns, before composition,
     conformance or status consistency. Nested governed content must also be immutable.
     `Judge.reconcile_expression` must not rewrite this result after presentation. An upstream
     inconsistency routes through Orchestrator to a fresh Judge pass or justified replan.
3. `AnswerPackage` is the **read-only projection** of `AnswerProjection` consumed by both the
   Composer and Composition Conformance. Do **not** introduce a separate `JudgmentBundle` or
   `GovernedSnapshot`; `AnswerProjection`/`AnswerPackage` already serve those roles.
4. Compound Requirement coverage aggregates **every mandatory child**, in the Judge only:
   a parent is SATISFIED iff all mandatory children are SATISFIED. Supported children remain
   visible while siblings are unresolved; the parent and Goal must not overstate completion.
   RequirementAssessment records requirement_id, status, authority_status, support references,
   gaps, conflicts, reason and provenance/inspection references. AnswerPackage exposes these
   assessments, Requirements, propositions, Claims, QualifiedEvidence, unresolved mandatory
   meaning, limitations, GoalStatus and citation/origin mappings without reconstructing coverage.
5. Judge selective drill-down is generalized across evidence types (Web span/representation/
   document; structured record/envelope/raw fields; DB receipt/attestation; derived operation/
   operands). It inspects already-retrieved material only, never performs new acquisition, and
   never re-opens a hard gate.

## Invariants

- Only the Judge approves Claims and computes coverage/GoalStatus.
- Collected data is not approved data; artifact existence is not satisfaction.
- Conformance never re-Judges evidence; it checks representation only.
- Unsupported mandatory meaning must never become COMPLETE.

## Consequences

- F-06 closes by construction; Composer/Conformance share one coverage authority.
- `RequirementState` becomes a first-class assessment instead of an internal local.
- The Judge remains one class/role; no micro-service split.

## Non-goals

- No ObjectiveState introduction; `GoalStatus` + propositions remain the completion view.
- No change to WSA-01–09 semantics.
