"""Backend-owned, Requirement-local semantic changes from confirmed user answers.

This is not a second interpreter: the existing candidate/authority/conformance paths
still decide analytical meaning. A clarification changes only user-owned coverage
permission or invalidates a candidate; model restatements never edit Requirements.
"""
from __future__ import annotations

from app.capabilities.analytics.conformance import digest
from app.capabilities.analytics.catalog import PITCH_TABLE
from app.contracts.models import (
    AnalysisRequest, Clarification, ClarificationDelta, EntityMention, Goal,
    RequirementKind, SemanticInterpretation, TurnType, new_id,
)


def target_digest(goal: Goal, requirement_id: str, candidate_id: str, source_span: str) -> str:
    owner = next((r for r in goal.requirements if r.id == requirement_id), None)
    candidate = next((r for r in goal.requirements if r.id == candidate_id), None)
    competing = [r.model_dump(mode="json") for r in goal.requirements
                 if r.id != candidate_id and r.derivation_kind == 'governed_semantic_candidate'
                 and isinstance(r.value, dict) and any(
                     s and (s in source_span or source_span in s)
                     for s in r.value.get('source_spans') or [])]
    return digest(dict(goal=goal.id, owner=owner.model_dump(mode="json") if owner else None,
                       candidate=candidate.model_dump(mode="json") if candidate else None,
                       competing=competing,
                       prior_deltas=[d.model_dump(mode='json') for d in goal.semantic_deltas
                                     if d.requirement_id == requirement_id or d.candidate_id == candidate_id],
                       source_span=source_span))


def make_semantic(goal: Goal, user_answer: str) -> SemanticInterpretation:
    """Planning view of the prior authoritative state, not a new semantic parse."""
    semantic = SemanticInterpretation(raw_query=goal.raw_query, understanding=goal.statement,
                               turn_type=TurnType.CLARIFICATION_ANSWER.value,
                               structured_hints={"clarification_answer": user_answer})
    for req in goal.requirements:
        if req.kind == RequirementKind.ANALYSIS:
            value = req.value if isinstance(req.value, dict) else {}
            objective = str(value.get("objective") or req.statement)
            semantic.analysis_requests.append(AnalysisRequest(text=objective, objective=objective))
        if req.kind == RequirementKind.ENTITY and isinstance(req.value, dict) and req.value.get("mention"):
            semantic.entities.append(EntityMention(mention=req.value["mention"],
                                                   type_hint=req.value.get("type_hint", "player")))
    return semantic


def closed_domain_replacements(candidate: dict, unresolved: str) -> list[tuple[str, dict]]:
    """Offer only small, existing schema-enumerated alternatives, not guessed language.

    An option is a proposal, never proof until selected by the user. Arbitrary
    corrections remain unresolved rather than model-derived or silently discarded.
    """
    filters = candidate.get('filters') or []
    if len(filters) != 1 or filters[0].get('op') != 'COMPARE' or filters[0].get('comparator') != '=':
        return []
    old = filters[0]
    spec = PITCH_TABLE.fields.get(old.get('field'))
    if spec is None or not (1 < len(spec.enum_values) <= 3) or old.get('value') not in spec.enum_values:
        return []
    result = []
    for value in spec.enum_values:
        if value == old['value']:
            continue
        proposed = {**candidate, 'filters': [{**old, 'value': value}]}
        footprint = {k: v for k, v in proposed.items()
                     if k not in {'source_spans', 'ignored_spans', 'attributions'}}
        result.append((f"Instead only {footprint}; no other condition in {unresolved!r}", proposed))
    return result


def apply_semantic_delta(goal: Goal, pending: Clarification, action: str, user_answer: str) -> Goal:
    """Apply a server-confirmed option to a copy, preserving independent Requirements.

    No substring removal of the original query. Conformance checks any confirmed
    source accounting against the candidate and parent on every subsequent use.
    """
    if (pending.goal_id != goal.id or not pending.candidate_id or not pending.requirement_id
            or not pending.source_span or not pending.target_digest):
        raise ValueError("stale or non-analytical clarification")
    if target_digest(goal, pending.requirement_id, pending.candidate_id, pending.source_span) != pending.target_digest:
        raise ValueError("stale clarification semantic target")
    if action not in pending.option_actions:
        raise ValueError("unrecognised clarification action")
    owner = next((r for r in goal.requirements if r.id == pending.requirement_id
                  and r.kind in {RequirementKind.ANALYSIS, RequirementKind.EVIDENCE}), None)
    candidate = next((r for r in goal.requirements if r.id == pending.candidate_id
                      and r.derivation_kind == "governed_semantic_candidate"), None)
    if (owner is None or candidate is None or owner.id not in candidate.parent_requirement_ids
            or not isinstance(candidate.value, dict)
            or pending.source_span not in (candidate.value.get("source_spans") or [])):
        raise ValueError("clarification target lost its authoritative candidate")
    updated = goal.model_copy(deep=True)
    updated.id = new_id("goal")
    for proposition in updated.propositions:
        proposition.goal_id = updated.id
    updated.parent_goal_id = goal.id
    updated.turn_type = TurnType.CLARIFICATION_ANSWER.value
    updated.semantic_deltas.append(ClarificationDelta(
        action=action, requirement_id=owner.id, candidate_id=candidate.id,
        source_span=pending.source_span, source_goal_id=goal.id,
        user_answer=user_answer, target_digest=pending.target_digest,
        confirmed_residue=pending.confirmed_residue))
    replacement = None
    if action == 'REPLACE_INTERPRETATION' and user_answer in pending.options:
        index = pending.options.index(user_answer)
        if index < len(pending.option_replacements):
            replacement = pending.option_replacements[index]
    if replacement is not None:
        # Validate the backend option against the *current* finite catalog, not
        # solely against serialized pending state. No free-text/model correction
        # is trusted to write a Requirement.
        allowed = [v for _, v in closed_domain_replacements(candidate.value, '')]
        if replacement not in allowed:
            raise ValueError('invalid clarification replacement')
    invalidated = {candidate.id} if action == "REPLACE_INTERPRETATION" else set()
    if action == "DISCHARGE_RESIDUE":
        # "Only this candidate" also rules out competing model candidates claiming
        # any part of the same literal user region. Independently owned Requirements
        # (entity, season, Measure and separate user tasks) are untouched.
        for other in updated.requirements:
            if other.id == candidate.id or other.derivation_kind != "governed_semantic_candidate":
                continue
            other_value = other.value if isinstance(other.value, dict) else {}
            if any(s and (s in pending.source_span or pending.source_span in s)
                   for s in other_value.get("source_spans") or []):
                invalidated.add(other.id)
    if invalidated:
        updated.requirements = [r for r in updated.requirements if r.id not in invalidated]
        updated.propositions = [p for p in updated.propositions
                                if not set(p.requirement_ids) & invalidated]
    if replacement is not None:
        corrected = candidate.model_copy(deep=True)
        corrected.id = new_id('req')
        corrected.value = replacement
        corrected.source = 'clarification'
        from app.contracts.models import RequirementProvenance
        from app.semantic.provenance import message_digest
        corrected.provenance = RequirementProvenance(
            origin="VALIDATED_CLARIFICATION", message_id=updated.id,
            bound_message=updated.raw_query, message_digest=message_digest(updated.raw_query),
            user_span=pending.source_span, clarification_target_digest=pending.target_digest,
            clarification_answer=user_answer,
        ).frozen_snapshot()
        corrected.supersedes = candidate.id
        updated.requirements.append(corrected)
        # The chosen option explicitly confirms that this *new* footprint, and
        # no other meaning in this region, is the user's intended interpretation.
        updated.semantic_deltas.append(ClarificationDelta(
            action='DISCHARGE_RESIDUE', requirement_id=owner.id,
            candidate_id=corrected.id, source_span=pending.source_span,
            source_goal_id=goal.id, user_answer=user_answer,
            target_digest=pending.target_digest,
            confirmed_residue=pending.confirmed_residue))
    # The prior ledger is retained for audit/replan; conformance digests the new
    # authoritative delta and permits only a materially changed context to retry.
    return updated
