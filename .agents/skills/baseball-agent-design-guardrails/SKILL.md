---
name: baseball-agent-design-guardrails
description: >
  Apply Baseball Agent architecture and semantic-safety constraints when designing,
  implementing, debugging, or reviewing semantic interpretation, natural-language
  parsing, Requirement modeling or authority, analytical conformance, Planner or
  Judge behavior, Artifact/Evidence governance, Shared Knowledge, clarification,
  partial results, or frontend/backend semantic interaction contracts. Use for
  architecture review, semantic parser/test failures, new paraphrases or aliases,
  user-language regex/keyword additions (including reviews of a regex that makes a
  test pass), COMPLETE/LIMITED/PARTIAL changes, and changes to what reaches execution
  or frontend analytical results. Routine formatting, documentation typos, CSS/button
  styling, and development-only formatting dependencies do not normally need this skill.
---

# Baseball Agent Design Guardrails

Use this skill as an architectural constraint, not as a feature specification.
Before changing relevant production behavior, read
[design principles](references/design-principles.md) and the
[review checklist](references/review-checklist.md). For a review, apply the same
constraints without assuming permission to repair production code.

## Required workflow

1. Identify the architectural layer that owns the behavior and state the semantic
   or evidence invariant at issue. Read the applicable architecture/SPEC/ADRs.
2. Classify the input as open-world human language or closed-world governed
   execution semantics. Trace what authority every proposed signal gains downstream.
3. Follow Interpret → Ground → Clarify: use context and stable Shared Knowledge,
   bind entities, schema, Measures, temporal scope and population, validate the
   candidate, then ask about remaining material ambiguity.
4. Repair the semantic abstraction before patching wording. Preserve all
   answer-changing clauses, unresolved meaning and resolution history.
5. Diagnose → repair → test → inspect → repeat for authorized, in-scope work.
   Test the mechanism; include fresh paraphrase/adversarial/metamorphic evaluation
   when language behavior changes. Measure both safety and utility.
6. Apply the review checklist before completion. Report evidence, remaining
   limitations and unresolved failures; do not freeze with known critical defects.

## Non-negotiable invariants

- Open-world language, closed-world execution. LLM proposes meaning; deterministic
  code grounds and validates it. Lexical/task-word signals are weak hints, never
  proof of authority, independence, UNDERSTOOD, COMPLETE or permission to execute.
- Do not solve open-ended language through vocabulary/regex/typo accretion. Regex
  is valid for genuinely finite syntax/domain rules. Centralize stable terminology;
  validate recovered typo meaning instead of fuzzy-matching text to executable fields.
- Partial resolution is not full clause UNDERSTOOD. Conserve every mandatory
  meaning and explicitly justify ignored content; retain relevant residue.
- Provenance is not semantic proof. Parent IDs, derivation labels, proof prose,
  Planner/Need metadata and model candidates cannot launder Requirement authority.
- Collected Data != Approved Data. Compare independently inspectable requested and
  executed contracts; require validation, conformance, lineage and Judge approval.
- Requirement, RelationSemantics, Proposition, Artifact, Planner and Judge have
  separate responsibilities. Approval is Requirement-local where independence is
  proven; completion is Goal-level. Retain independent supported results and block
  dependent ones. Unsupported mandatory meaning must never become COMPLETE.
- TEMPORARILY_UNRESOLVED remains actionable across replans and clarification.
  Retry only with materially new information. Shared Knowledge assists semantics;
  mutable facts still require Artifact → Evidence → Judge governance.
- Frontend collects intent; backend owns semantics. Backend defines opaque
  clarification options and approved presentation state. Model-generated executable
  UI is prohibited; use trusted components for governed Artifact types.
- Architecture-first development; tests are evidence, not the semantic specification.
  Production must not know fresh evaluation vocabulary or be patched to frozen cases.
- Correctness, evidence integrity, authority and credential/database/execution
  security are hard invariants. Within them prioritize functional completeness,
  answer/evidence quality, then performance; defensive rejection alone is not success.

## Completion

Run relevant regression and fresh mechanism-level checks, and complete
[references/review-checklist.md](references/review-checklist.md). Check that
`wrong semantic COMPLETE = 0` in the evaluated cases, without claiming tests prove
universal correctness. Do not claim partial-result independence is implemented
without evidence. Document the limits of the review or repair.

The references are self-contained. Repository architecture, SPEC, ADRs and audits
add implementation context; historical report filenames are not prerequisites.
