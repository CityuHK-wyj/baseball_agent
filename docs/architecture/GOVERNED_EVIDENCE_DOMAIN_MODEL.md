# Governed Evidence and Authority Domain Model

Status: **current baseline navigation**, verified against
`1d8cb3ad0ec786b48c82de830c6ad68b229c1511`.
The original accepted migration decisions remain in the
[ADR migration set](../adr/drafts/README.md); their staging history is not rewritten.

Use the [Domain Concept Registry](DOMAIN_CONCEPT_REGISTRY.md) for exact symbols,
construction/consumption, lifecycle, authority and serialization. Use the
[Authority Model](AUTHORITY_MODEL.md) for decision ownership and forbidden promotions.
These are the canonical current references, rather than a duplicate type catalog.

## Implemented boundary

- `RequirementBuilder` produces `UserRequirement` and bound
  `RequirementProvenance`. Ancestry does not itself establish semantic entailment.
- `Artifact`, `Export`, `EvidenceSpan` and `EvidenceFinding` are collected candidate
  material. Tool `ACCEPTED` is not final factual approval.
- Judge-local `assess_artifacts` produces `ArtifactAssessment` eligibility/proofs;
  only Judge owns `RequirementAssessment`, factual `Claim`, `QualifiedEvidence`
  disposition and final `AnswerProjection`.
- `Judge.evaluate` returns a detached recursively immutable projection. The package
  builder consumes its assessments, copies governed content and adds read-only
  presentation navigation. Composer and Conformance receive the same `AnswerPackage`.
- `ComposerDraft` is untrusted. `ConformanceResult` checks representation only.
  Orchestrator owns bounded regeneration, justified acquisition continuation,
  integrity recovery and fallback; consistency never rewrites governed state.

## Labels that are not additional implementation types

Requirement means `UserRequirement`; ContentArtifact means `RetrievedDocument`;
Shared Knowledge uses `KnowledgeItem`; runtime context is `ExecutionContext`.
There is no implemented `EvidenceItem`, `EvidenceCatalog`, `JudgmentBundle` or
`GovernedSnapshot` class, and no compatibility alias `RequirementState`.
`AnswerProjection` and `AnswerPackage` already serve the governed-state and
presentation-snapshot responsibilities.

The formerly proposed `RequirementAssessment.provenance` field is not present:
ancestry resides on the Requirement, while assessments contain authority/coverage,
support references, gaps/conflicts and inspection IDs. `ArtifactAssessment` is
assembled during the Judge pass, not an independently authoritative pre-Judge pipeline.

## Implemented versus target

Seven capability specs remain visible alongside two domain-family labels. Shared
Knowledge is stable interpretation context, not a mutable-fact shortcut. Derived
factual outputs require finite operation contracts and recursive exact-cell ancestry;
arbitrary Python output cannot mint Claims. Goal-wide budget/cache targets are not
fully implemented, and active browser networking is unavailable. See
[Implementation Status](../engineering/IMPLEMENTATION_STATUS.md) for current limits,
partial-result behavior and open U-1/U-2/F-3/F-5/F-4 work.
