"""Backend-owned Requirement ancestry. A bound quote is not semantic entailment."""
from hashlib import sha256

from app.contracts.models import RequirementProvenance


def message_digest(message: str) -> str:
    return sha256(message.encode()).hexdigest()


def bind_current_user_provenance(goal, *, parent_goal=None):
    """Called at the backend Requirement construction boundary, never on Planner metadata.

    Normalized statements may paraphrase. Their root is the actual bound message/quote,
    not an assertion that the normalized text literally appeared in that message.
    """
    raw = goal.raw_query
    for requirement in goal.requirements:
        value = requirement.value if isinstance(requirement.value, dict) else {}
        span = str(value.get("source_text") or "")
        if not span or span not in raw:
            span = raw
        contextual_entity = requirement.kind.value == "ENTITY" and requirement.entity_ref is not None and requirement.entity_ref.is_contextual()
        contextual_meaning = value.get("meaning_origin") == "DERIVED_CONTEXT"
        origin = "DERIVED_CONTEXT" if requirement.source == "inferred" or contextual_entity or contextual_meaning else "CURRENT_USER_MESSAGE"
        antecedent = ((requirement.entity_ref.antecedent or requirement.entity_ref.canonical_name or "")
                      if contextual_entity else parent_goal.raw_query if contextual_meaning and parent_goal else "")
        requirement.provenance = RequirementProvenance(
            origin=origin, message_id=goal.id, message_digest=message_digest(raw),
            bound_message=raw, user_span=span, parent_requirement_ids=list(requirement.parent_requirement_ids),
            context_antecedent=antecedent,
        ).frozen_snapshot()


def provenance_valid(requirement, *, bound_message=None, clarification_deltas=()):
    provenance = requirement.get("provenance")
    if not isinstance(provenance, dict):
        return False
    raw = provenance.get("bound_message")
    span = provenance.get("user_span")
    if not isinstance(raw, str) or not raw.strip() or not isinstance(span, str) or not span or span not in raw:
        return False
    if bound_message is not None and raw != bound_message:
        return False
    if not provenance.get("message_id") or provenance.get("message_digest") != message_digest(raw):
        return False
    origin = provenance.get("origin")
    source = requirement.get("source", "user")
    if source == "clarification":
        return origin == "VALIDATED_CLARIFICATION" and any(
            delta.get("candidate_id") == requirement.get("id")
            and delta.get("target_digest") == provenance.get("clarification_target_digest")
            and delta.get("user_answer") == provenance.get("clarification_answer")
            and bool(delta.get("target_digest")) for delta in clarification_deltas
        )
    if origin == "CURRENT_USER_MESSAGE":
        return source == "user"
    if origin == "DERIVED_CONTEXT":
        # Identity and continuation of prior USER intent may inform interpretation.
        # This is not authority for an assistant/context factual assertion. The backend
        # alone binds the actual prior-user antecedent when constructing the new Goal.
        if source == "user":
            kind = requirement.get("kind")
            value = requirement.get("value") or {}
            derived_user_intent = kind in {"ANALYSIS", "EVIDENCE"} and isinstance(value, dict) and value.get("meaning_origin") == "DERIVED_CONTEXT"
            return bool(provenance.get("context_antecedent")) and (kind == "ENTITY" or derived_user_intent)
        return source == "inferred" and bool(provenance.get("parent_requirement_ids"))
    return False
