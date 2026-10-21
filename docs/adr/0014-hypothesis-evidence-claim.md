# ADR 0014 — Hypothesis, Evidence and Claim are Distinct

Status: accepted (2026-09-21)

## Context

The system correctly requires that an LLM must not present an unverified fact as reliable
evidence. It over-applied that rule: because there was no representation of a *hypothesis*,
any candidate entity, candidate explanation or candidate investigation direction had to be
verified before it could be used at all. On a dictionary miss the entity tool returned
`FAILED` with no artifact, so reasoning and evidence discovery stopped even though the
hypothesis was still useful for choosing the next source.

## Decision

Introduce three explicit epistemic categories.

- **Hypothesis** — a candidate explanation, entity, relation or retrieval direction. May
  originate from an LLM prior, the user's wording, conversation context, a non-authoritative
  search snippet, or unverified Knowledge. A hypothesis may guide the next action. It is
  never Verified Evidence.
- **Evidence** — information obtained through a database, external API, fetched web page,
  approved knowledge or another trusted execution result. Evidence carries source,
  provenance, relevant scope, verification state, supporting content and a reference.
- **Claim** — a statement prepared for the final answer. Every claim is labelled with an
  `EpistemicStatus`:
  - `SUPPORTED` — backed by a specific piece of evidence;
  - `INFERRED` — derived from supported facts, but the derivation itself is not direct evidence;
  - `BACKGROUND` — the model's own knowledge, explicitly labelled and never given a citation;
  - `HYPOTHESIS` — a candidate explanation that is offered as such and still needs testing;
  - `UNKNOWN` — the system explicitly cannot say.

`Hypothesis` is a first-class contract (`app/contracts/models.py`) carried by
`SemanticInterpretation` and by the entity-resolution artifact. `Claim.epistemic` is set by the
Judge. `Reference` continues to point only at real evidence.

## Invariants

1. A hypothesis may be passed to the Planner and to discovery tools.
2. A hypothesis may never be emitted as `SUPPORTED`.
3. A `SUPPORTED` claim must carry at least one evidence reference.
4. A `BACKGROUND` claim must not carry a citation and must be rendered as unverified.
5. Precise numerical values, current status, disputed historical specifics and concrete
   allegations are never answered from `BACKGROUND`; they require evidence.

## Consequences

- The Planner can act on a candidate subject before it is resolved to a canonical id.
- The final answer can distinguish fact, calculation, inference, and unverified background.
- The composer gains an explicit, bounded way to explain, while the anti-fabrication rule
  stays in force.
