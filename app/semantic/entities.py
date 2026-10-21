"""Contextual entity references: merge semantic mentions with accepted context (F1).

The semantic interpreter understands *meaning*; it may name a carried entity explicitly
("Mookie Betts") while still echoing the user's surface pronoun ("他"). Requirement and
request construction must not collapse those two facts. This module builds a deterministic,
provenance-bearing :class:`EntityReference` for every subject the current turn refers to,
merging:

- the user's surface form (which may be a pronoun);
- an explicit entity constraint emitted by the interpreter;
- accepted conversation context (a canonical identity resolved in a previous turn).

It never promotes an unverified candidate to a canonical id. When a contextual reference
has more than one plausible antecedent it is marked ambiguous and left unresolved so the
runtime can ask the user instead of guessing (ADR 0018).
"""

from __future__ import annotations

import re
from typing import Any

from app.contracts.models import (
    ConversationState,
    EntityMention,
    EntityReference,
    Goal,
    IdentityLevel,
    Reference,
    ReferenceType,
    RequirementKind,
    SemanticInterpretation,
)

# Surfaces that only make sense relative to a previous turn.
_PRONOUN_SURFACES = {
    "他", "她", "它", "他们", "她们", "它们", "其", "此人", "该球员", "这位球员", "这名球员",
    "那位球员", "这名选手", "该选手", "这名投手", "这名打者", "该队", "这支球队", "这个球员",
    "he", "she", "they", "it", "him", "her", "them", "his", "their",
    "this player", "that player", "the player", "same player", "this team", "that team",
    "the team", "same team", "this guy", "that guy",
}

# Short CJK pronoun-adjacent words that may be embedded in a longer mention.
_PRONOUN_PATTERN = re.compile(
    r"^(他|她|它|他们|她们|它们|此人|该(球员|选手|投手|打者|队)|这(位|名|个)?(球员|选手|投手|打者)|那(位|名|个)?(球员|选手|投手|打者))$"
)


def is_contextual_surface(text: str) -> bool:
    """True when a mention is a pronoun / ellipsis that needs an antecedent."""
    surface = (text or "").strip()
    if not surface:
        return False
    if surface.lower() in _PRONOUN_SURFACES:
        return True
    return bool(_PRONOUN_PATTERN.match(surface))


def build_entity_references(
    semantic: SemanticInterpretation,
    *,
    state: ConversationState | None = None,
    parent_goal: Goal | None = None,
) -> list[EntityReference]:
    """Build the full reference list for a turn from mentions + constraints + context."""
    constraint_refs = _constraint_references(semantic)
    context = _context_identities(state, parent_goal)
    references: list[EntityReference] = []
    represented: set[str] = set()

    for mention in semantic.entities:
        reference = _reference_for_mention(mention, constraint_refs, context, parent_goal)
        references.append(reference)
        represented.add(_key(reference.surface))
        if reference.canonical_name:
            represented.add(_key(reference.canonical_name))

    # An explicit entity constraint the interpreter emitted but did not echo as a mention
    # (e.g. the user said "他" and the model resolved it to "Mookie Betts").
    for constraint_ref in constraint_refs:
        if _key(constraint_ref.surface) in represented:
            continue
        if constraint_ref.canonical_name and _key(constraint_ref.canonical_name) in represented:
            continue
        references.append(constraint_ref)
        represented.add(_key(constraint_ref.surface))
        if constraint_ref.canonical_name:
            represented.add(_key(constraint_ref.canonical_name))

    return references


def mentions_from_requirements(goal: Goal) -> list[dict[str, Any]]:
    """The entity_resolution request mentions, carrying canonical fields when known."""
    mentions: list[dict[str, Any]] = []
    for requirement in goal.requirements:
        if requirement.kind != RequirementKind.ENTITY:
            continue
        if requirement.entity_ref is not None:
            mentions.append(requirement.entity_ref.to_mention())
            continue
        if isinstance(requirement.value, dict) and requirement.value.get("mention"):
            mentions.append(
                {
                    "mention": requirement.value["mention"],
                    "type_hint": requirement.value.get("type_hint", "unknown"),
                }
            )
    return mentions


# ------------------------------------------------------------------ internals
def _reference_for_mention(
    mention: EntityMention,
    constraint_refs: list[EntityReference],
    context: list[dict[str, Any]],
    parent_goal: Goal | None,
) -> EntityReference:
    surface = mention.mention
    contextual = bool(mention.carried) or is_contextual_surface(surface)
    reference = EntityReference(
        surface=surface,
        type_hint=mention.type_hint or "unknown",
        source="context" if contextual else "user",
        reference=Reference(
            ref_type=ReferenceType.USER_SPAN,
            ref_id=mention.ref or surface,
            source="user",
            span=surface,
        ),
    )

    explicit_hint = _matching_constraint(constraint_refs, surface)
    if explicit_hint is None and contextual and len(constraint_refs) == 1:
        # The interpreter named the carried entity but did not echo it as a mention
        # (e.g. the user said "他" and the constraint says "Mookie Betts").
        if not is_contextual_surface(constraint_refs[0].surface):
            explicit_hint = constraint_refs[0]
    if contextual:
        antecedent = _pick_antecedent(context)
        if antecedent is None and len(context) > 1:
            reference.ambiguous = True
            reference.options = [
                str(identity.get("name") or identity.get("mention") or identity.get("id"))
                for identity in context
            ]
        if antecedent is not None:
            _apply_identity(reference, antecedent)
            reference.carried_from_goal = antecedent.get("goal_id") or (
                parent_goal.id if parent_goal else None
            )
            reference.antecedent = antecedent.get("mention") or antecedent.get("name")
        if explicit_hint is not None and reference.canonical_name is None:
            reference.canonical_name = explicit_hint.canonical_name
            reference.canonical_id = explicit_hint.canonical_id
            reference.namespace = explicit_hint.namespace
            reference.status = _status_for(explicit_hint.canonical_id, explicit_hint.canonical_name)
            reference.identity_verified = False
            reference.source = "interpreter"
            if parent_goal is not None:
                reference.carried_from_goal = parent_goal.id
    elif explicit_hint is not None:
        reference.canonical_name = explicit_hint.canonical_name
        reference.canonical_id = explicit_hint.canonical_id
        reference.namespace = explicit_hint.namespace
        reference.status = _status_for(explicit_hint.canonical_id, explicit_hint.canonical_name)
        reference.identity_verified = explicit_hint.identity_verified
        reference.source = explicit_hint.source
    return reference


def _apply_identity(reference: EntityReference, antecedent: dict[str, Any]) -> None:
    canonical_id = antecedent.get("id")
    name = antecedent.get("name") or antecedent.get("mention")
    if canonical_id:
        reference.canonical_id = str(canonical_id)
        reference.namespace = antecedent.get("namespace") or "MLBAM"
        reference.status = IdentityLevel.CANONICAL
        reference.identity_verified = True
    elif name:
        reference.canonical_name = str(name)
        reference.status = IdentityLevel.RESOLVED
        reference.identity_verified = False
    if name and not reference.canonical_name:
        reference.canonical_name = str(name)


def _status_for(canonical_id: str | None, canonical_name: str | None) -> IdentityLevel:
    if canonical_id:
        return IdentityLevel.CANONICAL
    if canonical_name:
        return IdentityLevel.RESOLVED
    return IdentityLevel.CANDIDATE


def _constraint_references(semantic: SemanticInterpretation) -> list[EntityReference]:
    references: list[EntityReference] = []
    for constraint in semantic.constraints:
        if constraint.kind != "entity" or constraint.value in (None, ""):
            continue
        value = constraint.value
        if isinstance(value, dict):
            name = value.get("canonical_name") or value.get("name") or value.get("mention")
            canonical_id = value.get("id") or value.get("canonical_id")
            namespace = value.get("namespace")
            type_hint = value.get("type_hint", "unknown")
            surface = value.get("mention") or name
        else:
            name = str(value)
            canonical_id = None
            namespace = None
            type_hint = "unknown"
            surface = name
        surface = str(surface or name or "").strip()
        name = str(name).strip() if name else ""
        if not surface and not name:
            continue
        if surface and is_contextual_surface(surface) and not name:
            # A bare pronoun constraint carries no identity; it needs context, not a name.
            continue
        references.append(
            EntityReference(
                surface=surface or name,
                type_hint=type_hint,
                canonical_id=str(canonical_id) if canonical_id else None,
                canonical_name=name or None,
                namespace=str(namespace) if namespace else ("MLBAM" if canonical_id else None),
                status=_status_for(
                    str(canonical_id) if canonical_id else None, name or None
                ),
                identity_verified=bool(canonical_id),
                source="interpreter",
                reference=Reference(
                    ref_type=ReferenceType.USER_SPAN,
                    ref_id=constraint.source_ref or surface or name,
                    source="interpreter",
                    span=name or surface,
                ),
            )
        )
    return references


def _matching_constraint(constraint_refs: list[EntityReference], surface: str) -> EntityReference | None:
    key = _key(surface)
    if not key:
        return None
    for reference in constraint_refs:
        if key == _key(reference.surface) or key == _key(reference.canonical_name):
            return reference
    return None


def strip_context_contamination(
    semantic: SemanticInterpretation,
    *,
    state: ConversationState | None = None,
    parent_goal: Goal | None = None,
) -> SemanticInterpretation:
    """Drop context-inherited entities from an INDEPENDENT turn (F1, ADR 0018).

    When the deterministic classifier says a turn is independent, an entity that is not
    anchored in the current user text but exactly matches a prior context identity is a
    context leak from the interpreter prompt, not a subject the user asked about. Such
    entities/constraints are removed. A translation where the interpreter preserves the
    user's own mention is anchored and therefore kept.
    """
    identities = _context_identities(state, parent_goal)
    if not identities:
        return semantic
    labels: set[str] = set()
    for identity in identities:
        for key in ("id", "name", "mention"):
            value = identity.get(key)
            if value:
                labels.add(str(value))
    raw = semantic.raw_query or ""
    if any(_anchor_overlap(raw, label) for label in labels):
        # The user's own wording is close to a context identity; do not second-guess it.
        return semantic
    semantic.entities = [
        mention
        for mention in semantic.entities
        if mention.carried
        or is_contextual_surface(mention.mention)
        or _anchor_overlap(raw, mention.mention)
        or not _matches_any(mention.mention, labels)
    ]
    semantic.constraints = [
        constraint
        for constraint in semantic.constraints
        if not (
            constraint.kind == "entity"
            and constraint.value not in (None, "")
            and not _anchor_overlap(raw, _constraint_text(constraint))
            and _matches_any(_constraint_text(constraint), labels)
        )
    ]
    return semantic


def context_identity_labels(
    state: ConversationState | None, parent_goal: Goal | None
) -> list[str]:
    labels: list[str] = []
    for identity in _context_identities(state, parent_goal):
        name = identity.get("name") or identity.get("mention") or identity.get("id")
        if name:
            labels.append(str(name))
    return labels


def _constraint_text(constraint) -> str:
    value = constraint.value
    if isinstance(value, dict):
        return str(value.get("canonical_name") or value.get("name") or value.get("mention") or "")
    return str(value or "")


def _matches_any(text: str, labels: set[str]) -> bool:
    key = _key(text)
    if not key:
        return False
    return any(key == _key(label) or key in _key(label) or _key(label) in key for label in labels if label)


def _anchor_overlap(raw: str, label: str) -> bool:
    if not raw or not label:
        return False
    raw_l = raw.lower()
    label_l = label.lower()
    if label_l in raw_l:
        return True
    # CJK bigram overlap ("大谷" anchors "大谷翔平").
    for index in range(len(label_l) - 1):
        bigram = label_l[index:index + 2]
        if all("\u4e00" <= ch <= "\u9fff" for ch in bigram) and bigram in raw_l:
            return True
    # Latin word overlap.
    for word in re.split(r"[^0-9a-z]+", label_l):
        if len(word) >= 3 and word in raw_l:
            return True
    return False


def _context_identities(
    state: ConversationState | None, parent_goal: Goal | None
) -> list[dict[str, Any]]:
    identities: list[dict[str, Any]] = []
    if parent_goal is not None:
        for requirement in parent_goal.requirements:
            if requirement.kind != RequirementKind.ENTITY or requirement.entity_ref is None:
                continue
            reference = requirement.entity_ref
            if not (reference.canonical_id or reference.canonical_name):
                continue
            identities.append(
                {
                    "id": reference.canonical_id,
                    "name": reference.canonical_name,
                    "mention": reference.surface,
                    "role": None,
                    "namespace": reference.namespace,
                    "goal_id": parent_goal.id,
                    "verified": reference.identity_verified,
                }
            )
    if not identities and state is not None:
        for entry in state.recent_entities:
            if not entry.get("id"):
                continue
            identities.append(
                {
                    "id": str(entry["id"]),
                    "name": entry.get("name") or entry.get("mention"),
                    "mention": entry.get("mention") or entry.get("name"),
                    "role": entry.get("role"),
                    "namespace": entry.get("namespace") or "MLBAM",
                    "goal_id": entry.get("goal_id"),
                    "verified": True,
                }
            )
    return _dedupe(identities)


def _dedupe(identities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for identity in identities:
        key = str(identity.get("id") or _key(identity.get("name") or ""))
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(identity)
    return result


def _pick_antecedent(identities: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Only resolve when there is exactly one plausible antecedent.

    More than one distinct canonical/named identity is genuine ambiguity: resolving it
    would be a guess, so the caller must ask (ADR 0018).
    """
    if len(identities) == 1:
        return identities[0]
    return None


def _key(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())
