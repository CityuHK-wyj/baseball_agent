# ADR 0016 — Requirement Completion is Semantic, not Database-shaped

Status: accepted (2026-09-21)

## Context

`RequirementBuilder` mapped open questions into database-flavoured requirement kinds
(`ENTITY`, `SEASON`, `GAME_POPULATION`, `MEASURE`). The Judge then evaluated `SEASON`,
`GAME_POPULATION` and `TIME` only against `DATABASE_RESULT` artifacts, and a measure alias
found anywhere in an explanation question became a **required** `MEASURE`. The database
scope contract therefore defined what counted as a complete answer to every question.

## Decision

Keep the requirement kinds, but give each `UserRequirement` a semantic **completion** mode
that determines what can satisfy it:

- `VALUE` — a specific value that must come from a verified calculation (a requested
  measure). Only a machine-checked `actual_measures` declaration satisfies it.
- `SCOPE` — the answer must be pinned to a time/population window. Any grounded artifact
  whose scope carries that window can satisfy it; a database artifact is not required.
- `IDENTITY` — a subject must be identified. A candidate/resolved identity is enough for
  research; canonical resolution is required only by the capabilities (enforced by the
  Binder), not by the Judge.
- `GROUNDED` — at least one grounded artifact addresses the request.
- `EXPLANATION` — an explanatory answer. Grounded evidence strengthens it; when no grounded
  evidence is available the requirement may be reported as unverified background, clearly
  labelled, instead of failing the whole Goal.

`RequirementBuilder` assigns completion from the requirement's own semantics:

- `SEASON` / `TIME` / `GAME_POPULATION` → `SCOPE`;
- `ENTITY` → `IDENTITY`;
- `MEASURE` → `VALUE`;
- `ANALYSIS` → `GROUNDED`, or `EXPLANATION` when the question is explanatory/historical;
- `EVIDENCE` → `GROUNDED`.

For explanatory/historical questions, a measure alias found in the wording is recorded as a
**supporting** requirement (`required=False`), and a value-question marker
(`多少/是多少/how many/what is`) is required to make it mandatory. Definition questions
still never create measure requirements.

High-risk topics (precise numbers, current status, injuries/transactions, doping/scandal,
controversy, concrete allegations) force `EXPLANATION` requirements to require evidence and
disallow unverified background.

## Consequences

- A historical/explanatory question is not forced to produce an unrelated statistical
  artifact.
- A question that genuinely asks for a value still gets strict measure verification.
- A scope requirement can be reported `PARTIAL` (rather than failing) when the only
  supporting artifact cannot pin the scope.
