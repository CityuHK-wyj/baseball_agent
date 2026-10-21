# ADR 0017 — Planner Recovery, Scoped Binding Failures, and Claim-level Verification

Status: accepted (2026-09-21)

## Context

Three global gates turned a local failure into a final verdict:

1. A binding or capability failure produced a terminal BLOCKED Need and the graph stopped
   after one repair round.
2. `Judge._claims` used a global `database_claims_allowed` flag built from all entity and
   measure requirements, so one undecidable requirement erased every database claim.
3. A Goal with no `SUPPORTED` requirement became `FAILED`, even when the question was an
   explanation for which clearly-labelled background could be useful.

## Decision

**Planner recovery.** A failed Need yields a typed, structured reason (binding code,
capability code, retryability, objective) which is fed into the next Planner round. The
Planner may replace the failed capability with a different, permitted path (for example, a
database metric that is not locally derivable may still be investigated through web research
or an external API) and is told explicitly not to repeat the same failing request. Recovery
runs for a bounded number of rounds (`RuntimeConfig.max_rounds`, capped) and stops when the
core requirements are satisfied or no new work is proposed.

**Scoped binding failures.** `BindingFailure` stays typed and fail-closed, but it is scoped
to the Need and its dependency. A failed database binding blocks database execution only; it
must not prevent the Planner from investigating web or another source. The failure is
`retryable` metadata for the Planner, not a global failure.

**Claim-level verification.** The Judge evaluates each artifact's fitness for each claim:

- a `DATABASE_RESULT` claim requires the artifact's own scope to satisfy the goal's entity
  and measure obligations (an unresolved entity or a mismatched measure suppresses that
  artifact's claim, not all claims);
- a `WEB_EVIDENCE` claim requires a grounded, subject-relevant finding, otherwise that
  finding is dropped;
- independent supported claims survive when another requirement is unresolved.

A Goal is `COMPLETE` only when all required requirements are `SATISFIED`. It is `LIMITED`
when some are supported/partial **or** when a non-high-risk explanation can be answered with
clearly-labelled background. It is `FAILED` only when no permitted evidence path produced
anything and background is not allowed.

## Consequences

- One bad requirement no longer erases good evidence.
- The Planner can route around unavailable local data.
- The user gets a proportional answer: what is verified, what is inferred, what is
  background, and what is unknown.
