# ADR-draft 05 — Conversation Context vs User Authority

Status: **accepted architectural decision; staged implementation in progress.**
Relates to ADR 0009 (multi-turn), ADR 0018 (contextual entity reference), WSA-06 §30/§31.

## Context

`ConversationManager._context` marks assistant messages `authority="unverified_model_synthesis"`,
but the semantic pipeline can still turn assistant synthesis into a `source="user"` Requirement
because `RequirementBuilder` assigns user source from interpreter output (F-04). Genuine
coreference (pronouns, ellipsis) must keep working without laundering authority.

## Decision

1. **Message roles are typed at the conversation boundary.** Every context message carries a
   role/authority tag; only a bound *current user message* or a *validated clarification answer*
   can originate a `user_span`.
2. **Coreference is carried by provenance, not by re-sourcing.** Follow-ups use
   `EntityReference` (surface + canonical identity + antecedent) to resolve subjects. An
   assistant-mentioned entity may be carried as a *candidate identity*, never as a new factual
   Requirement.
3. **Context-derived Requirements are explicitly derived.** If meaning is inferred from prior
   turns rather than present in the current user span, the Requirement is `inferred` with
   authoritative parents and a governed derivation kind — subject to `resolve_authority`.
4. **Assistant prose is context, not evidence.** Any fact mentioned only by a prior assistant
   answer must be re-established through Artifact → Evidence → Judge.
5. Preserve pronouns, ellipsis, next-year follow-ups, named-entity changes, explicit user
   adoption/correction and backend-validated clarification. Distinguish user adoption of a
   proposition from mere reference to its entity. Independent turns do not inherit stale
   Requirements. Exact ancestry is not a requirement for literal normalized-string equality.

## Invariants

- Assistant history → user-origin Requirement is forbidden.
- Clarification answers are authoritative only through backend-defined options/deltas.
- Stale entities must not leak into independent turns.

## Consequences

- F-04 closes without weakening positive coreference/reporting controls.
- `strip_context_contamination` and `EntityReference` semantics are preserved.

## Non-goals

- No change to clarification option/delta ownership (ADR 0033 frontend boundary).
