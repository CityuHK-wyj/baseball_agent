"""Atomic Answer Proposition decomposition with deterministic structural validation.

The interpreter produces ``UserRequirement`` obligations. This module turns a requirement into
one or more **atomic, independently evidence-testable** propositions. Atomic means: one subject,
one requested relation, one scope, and a required-information slot that a single source could
support or refute. It deliberately does not decide truth and does not choose tools.

Two decomposition paths:

1. **Deterministic** (always available): normalizes the requirement's wording into a factual
   claim, maps its canonical relation label to a ``PropositionKind``, and splits a compound
   CAUSE+EFFECT obligation into one proposition per relation.
2. **LLM-assisted** (opt-in, structurally validated): used only when the wording genuinely
   requires natural-language decomposition. Every candidate is checked against the domain
   schema before it is accepted; malformed or invented output is rejected, not repaired into
   support.

Nothing here weakens the Evidence Verifier or Judge: propositions are claims whose support is
established later by an evidence-producing capability.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.contracts.models import (
    PropositionKind,
    RelationKind,
    RelationSemantics,
    RequirementCompletion,
    RequirementKind,
    UserRequirement,
)
from app.semantic.relations import relation_cues, relation_kinds_from_text

# Leading task-language that is an instruction, not a factual relation. Relation nouns
# (causes/effects/impact/reason/change) are deliberately NOT stripped: they are the factual
# relationship the proposition asserts.
_TASK_LEAD = (
    "find out", "look for", "search for", "tell me", "show me", "give me",
    "explanation", "narrative", "analyze", "analyse", "explain", "describe",
    "summarise", "summarize", "report", "identify", "determine", "locate",
    "retrieve", "provide", "list", "find", "show", "get",
    "查找", "寻找", "尋找", "找出", "解释", "解釋", "分析", "介绍", "介紹",
    "说明", "說明", "描述", "总结", "總結",
)
_LEAD_TRIM = ("the ", "a ", "an ", "of ", "about ", "regarding ", "for ", "on ", "as ", "that ")

# Canonical relation -> proposition kind. A relation always implies the kind of answer
# obligation it creates; identity/scope are handled by their requirement kinds.
_PROPOSITION_KIND: dict[RelationKind, PropositionKind] = {
    RelationKind.IDENTITY: PropositionKind.IDENTITY,
    RelationKind.SCOPE: PropositionKind.SCOPE,
    RelationKind.VALUE: PropositionKind.VALUE,
    RelationKind.AGGREGATION: PropositionKind.VALUE,
    RelationKind.COMPARISON: PropositionKind.COMPARISON,
    RelationKind.CAUSE: PropositionKind.EXPLANATION,
    RelationKind.EFFECT: PropositionKind.EXPLANATION,
    RelationKind.EXPLANATION: PropositionKind.EXPLANATION,
    RelationKind.QUOTE: PropositionKind.FACT,
    RelationKind.STATUS: PropositionKind.FACT,
    RelationKind.FRESHNESS: PropositionKind.FACT,
    RelationKind.EVENT: PropositionKind.EVENT,
    RelationKind.FACT: PropositionKind.FACT,
    RelationKind.GENERAL: PropositionKind.GROUNDED,
}

# Only this relation pair is split deterministically. Splitting every co-occurring cue would
# create artificial mandatory obligations (proposition explosion); CAUSE and EFFECT are
# genuinely independent facts about one event and are the demonstrated compound case.
_SPLIT_PAIRS: tuple[frozenset[RelationKind], ...] = (
    frozenset({RelationKind.CAUSE, RelationKind.EFFECT}),
)



@dataclass
class PropositionContext:
    """The Goal-level context a proposition inherits (subject, scope, goal id)."""

    goal_id: str = ""
    entities: list[str] = field(default_factory=list)
    season: int | None = None
    date_range: Any = None
    population: str | None = None

    @property
    def primary_subject(self) -> str:
        return self.entities[0] if self.entities else ""


def factual_core(text: str) -> str:
    """Strip leading task language from a requirement objective.

    ``explain the impact of X`` -> ``the impact of X``; ``explanation of a change`` ->
    ``a change``. The relation is preserved separately in ``operation``/``RelationSemantics``.
    Returns the original text when stripping would leave no content.
    """
    original = (text or "").strip()
    lowered = original.lower()
    changed = True
    while changed and lowered:
        changed = False
        for lead in _TASK_LEAD:
            if lowered.startswith(lead):
                end = len(lead)
                # Do not strip inside a longer word ("showcase").
                if len(lowered) > end and lowered[end].isalpha() and lowered[end] != " ":
                    continue
                original = original[end:].lstrip(" :,-")
                lowered = original.lower()
                changed = True
                break
    for trim in _LEAD_TRIM:
        while lowered.startswith(trim):
            original = original[len(trim):]
            lowered = original.lower()
    core = original.strip().strip("?.!。！？").strip()
    if not _content_tokens(core):
        return (text or "").strip()
    return core


def _strip_subject(text: str, entities: Iterable[str]) -> str:
    """Remove the subject entity from the required-information phrase.

    The subject is a separate, independently checked proposition slot. Leaving its name in the
    required-information text would let the verifier count it toward the required-token match
    and admit a passage that merely names the subject (a roster/status line, a citation). The
    relation/object tokens must carry the match on their own.
    """
    result = text or ""
    for entity in entities:
        entity = str(entity or "").strip()
        if entity and entity.lower() in result.lower():
            result = re.sub(re.escape(entity), " ", result, flags=re.IGNORECASE)
    result = re.sub(r"\b(about|of|for|on|regarding|to|by|or|and)\s*$", "", result.strip(), flags=re.IGNORECASE)
    result = re.sub(r"\s{2,}", " ", result).strip(" ,;:-?")
    return result or text


# Evidence-carrier nouns describe the artifact (a report, an update, a source) rather than the
# fact itself. Counting them as required tokens forces the verifier to demand the literal word
# "reports" even when the passage states the fact directly; freshness adjectives are scope, not
# relation words. They are removed so the required-information slot is the atomic relation object.
_CARRIER_NOUNS = (
    "reports", "report", "updates", "update", "sources", "source", "articles", "article",
    "news", "commentary", "coverage", "information", "details", "statements", "statement",
    "recent", "latest", "current",
)


def _strip_carriers(text: str) -> str:
    result = text or ""
    for word in _CARRIER_NOUNS:
        result = re.sub(rf"(?<![a-z]){re.escape(word)}(?![a-z])", " ", result, flags=re.IGNORECASE)
    result = re.sub(r"\s{2,}", " ", result).strip(" ,;:-?/")
    result = re.sub(r"\b(or|and|about|of|for)\s*$", "", result, flags=re.IGNORECASE).strip()
    return result if _content_tokens(result) else text


def decompose_requirement(
    requirement: UserRequirement,
    context: PropositionContext,
    *,
    llm: Any = None,
) -> list[dict[str, Any]] | None:
    """Return atomic proposition specs for a requirement, or ``None`` for kinds not handled.

    The specs use the same shape as explicitly supplied interpreter propositions, plus an
    optional ``relation_semantics`` override so a split proposition carries only its own
    relation label.
    """
    if requirement.kind not in {RequirementKind.ANALYSIS, RequirementKind.EVIDENCE}:
        return None
    text = _requirement_text(requirement)
    core = _strip_carriers(_strip_subject(factual_core(text), context.entities))
    relation_kinds = _relation_kinds(requirement, text)

    specs: list[dict[str, Any]] = []
    if llm is not None and getattr(llm, "configured", False) and _should_use_llm(relation_kinds):
        specs = _llm_specs(requirement, context, core, text, relation_kinds, llm)
    if not specs:
        specs = _deterministic_specs(requirement, context, core, relation_kinds)
    return validate_specs(specs, requirement, context)


def _deterministic_specs(
    requirement: UserRequirement,
    context: PropositionContext,
    core: str,
    relation_kinds: list[RelationKind],
) -> list[dict[str, Any]]:
    relational = [kind for kind in relation_kinds if kind in _PROPOSITION_KIND and kind not in {RelationKind.GENERAL}]
    split: set[RelationKind] = set()
    for pair in _SPLIT_PAIRS:
        if pair <= set(relational):
            split |= pair
    base_kind = _base_proposition_kind(requirement, relational)
    if len(split) >= 2:
        return [
            _spec_for_relation(requirement, context, core, kind, base_kind)
            for kind in sorted(split, key=lambda item: item.value)
        ]

    primary = next((kind for kind in relation_kinds if kind != RelationKind.GENERAL), RelationKind.GENERAL)
    if requirement.kind == RequirementKind.EVIDENCE and primary == RelationKind.GENERAL:
        primary = RelationKind.FACT
    return [_spec_for_relation(requirement, context, core, primary, base_kind)]


def _base_proposition_kind(requirement: UserRequirement, relation_kinds: list[RelationKind]) -> PropositionKind:
    """The answer-obligation kind, driven by the requirement's completion before its relation.

    A relation is descriptive; the completion (ADR 0016/0021) is what the user needs. This
    keeps an EXPLANATION requirement an EXPLANATION even when a comparison/aggregation cue also
    appears in its wording.
    """
    if requirement.kind == RequirementKind.EVIDENCE:
        return PropositionKind.FACT
    if requirement.completion == RequirementCompletion.EXPLANATION:
        return PropositionKind.EXPLANATION
    # A bare COMPARISON is a typed relation that needs governed operands. When the same
    # wording also asks for a computable aggregate/value ("counts ... and their
    # difference", "average split ... left vs right"), the aggregate is the operative
    # obligation; it does not become an operand-less comparative claim.
    computable = [kind for kind in relation_kinds if kind in {RelationKind.VALUE, RelationKind.AGGREGATION}]
    if RelationKind.COMPARISON in relation_kinds and computable:
        return _PROPOSITION_KIND[computable[0]]
    for kind in relation_kinds:
        mapped = _PROPOSITION_KIND.get(kind)
        if mapped is not None:
            return mapped
    return PropositionKind.GROUNDED


def _spec_for_relation(
    requirement: UserRequirement,
    context: PropositionContext,
    core: str,
    kind: RelationKind,
    proposition_kind: PropositionKind | None = None,
) -> dict[str, Any]:
    if proposition_kind is None:
        proposition_kind = _PROPOSITION_KIND.get(kind, PropositionKind.GROUNDED)
    # The relation is carried by ``relation_semantics`` (and its cues), never duplicated into
    # ``operation``: adding a generic relation word such as "status" to the required-token set
    # would raise the verifier's min-match threshold and wrongly reject a passage that states
    # the fact in its natural wording.
    operation = ""
    return {
        "statement": core,
        "kind": proposition_kind.value,
        "subject": context.primary_subject,
        "required_information": core,
        "operation": operation,
        "relation_semantics": _single_label_semantics(requirement.relation_semantics, kind, core),
    }


def _single_label_semantics(
    semantics: RelationSemantics | None, kind: RelationKind, core: str
) -> RelationSemantics | None:
    if semantics is None:
        return None
    # A split cause/effect obligation binds the event on the appropriate side of the
    # relation. The other side remains an evidence-testable unknown, never an assertion.
    anchor = re.sub(
        r"^(?:(?:causes?|reasons?|effects?|consequences?|impacts?|results?)\s+(?:and\s+\w+\s+)?(?:of|for)\s+|"
        r"what\s+(?:caused|led to|triggered)\s+|"
        r"what\s+happened\s+(?:because of|as a result of)\s+)",
        "", core, flags=re.IGNORECASE,
    ).strip()
    bound = anchor if anchor != core else ""
    source = semantics.relation_source or (bound if kind == RelationKind.EFFECT else "")
    target = semantics.relation_target or (bound if kind == RelationKind.CAUSE else "")
    return RelationSemantics(
        relation_kind=kind,
        relation_kinds=[kind],
        subject_refs=list(semantics.subject_refs),
        object_refs=list(semantics.object_refs),
        relation_source=source,
        relation_target=target,
        metric_refs=list(semantics.metric_refs),
        time_scope=dict(semantics.time_scope),
        population=semantics.population,
        predicate=semantics.predicate,
        comparison_direction=semantics.comparison_direction,
        polarity=semantics.polarity,
        semantic_cues=list(relation_cues(kind)),
        source_text=core or semantics.source_text,
    )


def _relation_kinds(requirement: UserRequirement, text: str) -> list[RelationKind]:
    semantics = requirement.relation_semantics
    if semantics is not None:
        labels = list(semantics.relation_kinds) if semantics.relation_kinds else [semantics.relation_kind]
        seen: list[RelationKind] = []
        for kind in labels:
            if kind not in seen:
                seen.append(kind)
        return seen
    return relation_kinds_from_text(text)


def _requirement_text(requirement: UserRequirement) -> str:
    value = requirement.value if isinstance(requirement.value, dict) else {}
    return str(value.get("objective") or requirement.statement)


# --------------------------------------------------------------------------- validation


def validate_specs(
    specs: Iterable[dict[str, Any]],
    requirement: UserRequirement,
    context: PropositionContext,
) -> list[dict[str, Any]]:
    """Reject structurally unusable candidate specs. Truth is never validated here."""
    accepted: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for spec in specs:
        if not isinstance(spec, dict):
            continue
        statement = str(spec.get("statement") or "").strip()
        required = str(spec.get("required_information") or "").strip()
        if not statement and not required:
            continue
        if not _content_tokens(required or statement):
            continue
        subject = str(spec.get("subject") or context.primary_subject or "").strip()
        relation = spec.get("relation_semantics")
        if relation is not None and not isinstance(relation, RelationSemantics):
            try:
                relation = RelationSemantics.model_validate(relation)
            except Exception:  # noqa: BLE001 - malformed semantics is unusable
                continue
        kind = str(spec.get("kind") or "GROUNDED").upper()
        relation_label = ""
        if isinstance(relation, RelationSemantics):
            relation_label = str(getattr(getattr(relation, "relation_kind", None), "value", "") or "")
        key = (kind, relation_label, subject.lower(), (required or statement).lower())
        if key in seen:
            continue
        seen.add(key)
        normalised = dict(spec)
        normalised["subject"] = subject
        if isinstance(relation, RelationSemantics):
            normalised["relation_semantics"] = relation
        accepted.append(normalised)
    return accepted


def validate_propositions(propositions: list[Any], context: PropositionContext) -> list[Any]:
    """Drop structurally unusable propositions and collapse semantic duplicates.

    The dedupe key is the factual identity (relation label, subject, required information,
    scope), not the surface statement, so two relation-specific propositions about the same
    object are both kept while a pure restatement is collapsed.
    """
    accepted: list[Any] = []
    seen: dict[str, Any] = {}
    for proposition in propositions:
        if proposition is None:
            continue
        statement = str(getattr(proposition, "statement", "") or "").strip()
        required = str(getattr(proposition, "required_information", "") or "").strip()
        if not statement and not required:
            continue
        if not _content_tokens(required or statement):
            continue
        semantics = getattr(proposition, "relation_semantics", None)
        relation_kind = ""
        if semantics is not None:
            relation_kind = getattr(getattr(semantics, "relation_kind", None), "value", "") or ""
        scope = getattr(proposition, "time_scope", None) or {}
        scope_key = f"{scope.get('season')}|{scope.get('date_range')}" if isinstance(scope, dict) else str(scope)
        key = (
            relation_kind,
            str(getattr(proposition, "subject", "") or "").lower(),
            (required or statement).lower(),
            str(getattr(proposition, "population", "") or "").lower(),
            scope_key,
        )
        # Share only exact source-scope obligations across Requirements. Other
        # slots retain their local owner: subject/measure words alone cannot prove
        # equivalence of reporting qualification, operation or Claim authority.
        import json
        meaning = proposition.model_dump(mode='json', exclude={
            'id', 'requirement_id', 'requirement_ids', 'statement', 'mandatory',
            'coverage', 'supporting_claim_ids', 'contradicting_claim_ids',
            'supporting_artifact_ids', 'evidence_finding_ids', 'gaps', 'note'})
        owner = None if getattr(proposition.kind, 'value', '') == 'SCOPE' else proposition.requirement_id
        identity = json.dumps([key, meaning, owner], sort_keys=True)
        if identity in seen:
            original = seen[identity]
            original.requirement_ids = [rid for rid in dict.fromkeys([
                *original.owner_requirement_ids, *proposition.owner_requirement_ids])
                if rid != original.requirement_id]
            original.mandatory = original.mandatory or proposition.mandatory
            continue
        seen[identity] = proposition
        accepted.append(proposition)
    return accepted


# --------------------------------------------------------------------------- LLM (opt-in)


_LLM_SYSTEM = """You decompose one baseball research requirement into atomic answer propositions.

Return JSON {"propositions":[{"statement":"...","kind":"FACT|EVENT|COMPARISON|EXPLANATION|GROUNDED|VALUE","subject":"...","required_information":"...","operation":"...","relation":"CAUSE|EFFECT|QUOTE|STATUS|FRESHNESS|COMPARISON|EXPLANATION|VALUE|AGGREGATION|EVENT|FACT|GENERAL","depends_on":[]}]}

Rules:
- Each proposition asserts exactly ONE factual relation that a source could support or refute.
- Use only the subject entity names provided. Never invent a player id, statistic, date or value.
- Do not include instructions such as "find", "explain", "analyze", "locate".
- Do not restate the question; state the claim whose support is required.
- Return 1 to 4 propositions. Do not duplicate a proposition.
"""


def _should_use_llm(relation_kinds: list[RelationKind]) -> bool:
    kinds = set(relation_kinds)
    return any(pair <= kinds for pair in _SPLIT_PAIRS)


def _llm_specs(
    requirement: UserRequirement,
    context: PropositionContext,
    core: str,
    text: str,
    relation_kinds: list[RelationKind],
    llm: Any,
) -> list[dict[str, Any]]:
    import json

    payload = {
        "requirement": text,
        "original_intent": requirement.original_intent,
        "entities": context.entities,
        "relation_kinds": [kind.value for kind in relation_kinds],
        "time_scope": {"season": context.season, "date_range": context.date_range},
        "population": context.population,
    }
    try:
        result = llm.complete_json(role="planner", system=_LLM_SYSTEM, user=json.dumps(payload, ensure_ascii=False), max_tokens=500)
    except Exception:  # noqa: BLE001 - decomposition failure must fall back, never fabricate
        return []
    raw = result.data.get("propositions") if isinstance(result.data, dict) else None
    if not isinstance(raw, list):
        return []
    specs: list[dict[str, Any]] = []
    for item in raw[:4]:
        if not isinstance(item, dict):
            continue
        relation_kind: RelationKind | None
        try:
            relation_kind = RelationKind(str(item.get("relation", "GENERAL")).upper())
        except ValueError:
            relation_kind = None
        required = str(item.get("required_information") or item.get("statement") or "").strip()
        statement = str(item.get("statement") or required).strip()
        subject = str(item.get("subject") or context.primary_subject or "").strip()
        if not required or not statement:
            continue
        # No invented numbers: every number in the candidate must occur in the requirement.
        if _has_invented_number(required + " " + statement, text):
            continue
        # The subject must be one of the provided entities (or a substring of one).
        if subject and context.entities and not _subject_allowed(subject, context.entities):
            continue
        try:
            kind = PropositionKind(str(item.get("kind", "GROUNDED")).upper()).value
        except ValueError:
            kind = PropositionKind.GROUNDED.value
        semantics = _single_label_semantics(requirement.relation_semantics, relation_kind or RelationKind.GENERAL, required)
        specs.append(
            {
                "statement": statement,
                "kind": kind,
                "subject": subject,
                "required_information": required,
                "operation": "",
                "relation_semantics": semantics,
            }
        )
    return specs


def _has_invented_number(candidate: str, source: str) -> bool:
    source_years = set(re.findall(r"\d+", source or ""))
    return any(number not in source_years for number in re.findall(r"\d+", candidate or ""))


def _subject_allowed(subject: str, entities: Iterable[str]) -> bool:
    low = subject.lower()
    return any(low in str(entity).lower() or str(entity).lower() in low for entity in entities)


# --------------------------------------------------------------------------- tokens


def _content_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for match in re.finditer(r"[0-9]+(?:\.[0-9]+)?|[a-z][a-z0-9'\-]+|[\u4e00-\u9fff]+", (text or "").lower()):
        token = match.group(0)
        if token in {"the", "a", "an", "of", "for", "and", "or", "to", "in", "on", "at", "is", "are"}:
            continue
        if all("\u4e00" <= ch <= "\u9fff" for ch in token):
            if len(token) >= 2:
                tokens.add(token)
        elif len(token) >= 3:
            tokens.add(token)
    return tokens
