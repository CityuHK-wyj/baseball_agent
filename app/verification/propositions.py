"""Answer Proposition construction and coverage (Phase 5B, ADR 0021).

A Goal is not COMPLETE because tools succeeded or artifacts exist; it is COMPLETE when
every *mandatory answer proposition* is covered by verified, scope-compatible support.

This module derives propositions from the frozen ``UserRequirement`` graph. It is
deliberately general: a proposition is an answer slot (identity, scope, value, event,
comparison, explanation, grounded need) with subject, time/population scope, required
information and dependencies. A requirement may carry an explicit ``propositions`` list
(produced by an interpreter or a decomposition step) to expand one compound obligation
into several slots; otherwise one requirement maps to one proposition. No trade example,
player or query is hardcoded.
"""

from __future__ import annotations

from typing import Any

from app.contracts.models import (
    AnswerProposition,
    CoverageState,
    Goal,
    PropositionKind,
    RelationSemantics,
    RequirementCompletion,
    RequirementKind,
    UserRequirement,
)
from app.semantic.decomposition import (
    PropositionContext,
    decompose_requirement,
    validate_propositions,
)
from app.semantic.relations import relation_cue_tokens
from app.verification.evidence_semantics import (
    derive_assertion_expectation,
    derive_authority_expectation,
    derive_temporal_expectation,
)

# Generic words that carry no proposition-specific content; they must not make a
# subject-matching page count as evidence for a required information slot.
_STOPWORDS = {
    "the", "a", "an", "of", "for", "and", "or", "to", "in", "on", "at", "is", "are",
    "was", "were", "be", "been", "his", "her", "their", "its", "this", "that", "these",
    "those", "what", "which", "who", "whom", "how", "why", "when", "where", "did", "does",
    "do", "with", "about", "into", "from", "as", "by", "over", "under", "between",
    "performance", "overall", "report", "investigate", "find", "explain", "information",
    "question", "answer", "结果", "表现", "如何", "什么", "为什么", "怎么", "是否", "关于",
    "相关", "报道", "解释", "介绍", "分析", "情况", "方面", "总体", "他的", "她的", "这个",
    "那个", "以及", "和", "与", "的", "了", "吗", "呢",
}


def build_propositions(goal: Goal, semantic=None, llm=None) -> list[AnswerProposition]:
    """Derive atomic answer obligations for a Goal from its frozen requirements.

    A requirement may carry explicit ``propositions`` (from the interpreter). Otherwise the
    canonical decomposer produces one or more atomic propositions. The LLM is only consulted
    when semantic decomposition needs natural-language understanding; its output is always
    structurally validated and falls back to the deterministic decomposition.
    """
    entities = _entity_context(goal)
    season, date_range = _time_context(goal)
    population = _population_context(goal)
    context = PropositionContext(
        goal_id=goal.id,
        entities=entities,
        season=season,
        date_range=date_range,
        population=population,
    )

    propositions: list[AnswerProposition] = []
    for requirement in goal.requirements:
        # A trusted typed comparison has one precise obligation. Open-language
        # proposition specs cannot replace it with a weaker GROUNDED slot or supply
        # their own operands/relation.
        if requirement.reporting_contract is not None:
            # One mandatory answer slot per explicitly requested observation. These are
            # not operation operands and cannot borrow a derived Claim's authority.
            for index, cell in enumerate(requirement.reporting_contract.cells):
                propositions.append(_with_expectations(AnswerProposition(
                    goal_id=goal.id, requirement_id=requirement.id,
                    statement=requirement.statement, kind=PropositionKind.VALUE,
                    mandatory=requirement.required, reporting_index=index,
                    subject=', '.join(cell.subjects), required_information=cell.measure,
                    time_scope=_scope(cell.season, cell.date_range.model_dump() if cell.date_range else None),
                    population=','.join(cell.game_types or []),
                    relation_semantics=requirement.relation_semantics), requirement))
            continue
        if requirement.operation_contract is not None and requirement.comparison is None:
            # A governed numerical operation is a VALUE obligation, not an unproved
            # boolean comparison. Other Requirements retain their own propositions.
            specs = [{"statement": requirement.statement, "kind": "VALUE",
                      "required_information": requirement.statement}]
        elif requirement.comparison is not None and requirement.kind == RequirementKind.ANALYSIS:
            specs = [{"statement": requirement.statement, "kind": "COMPARISON",
                      "required_information": requirement.statement}]
        else:
            specs = _explicit_specs(requirement)
            if not specs:
                specs = decompose_requirement(requirement, context, llm=llm)
        if specs is not None:
            for spec in specs:
                propositions.append(
                    _with_expectations(
                        _from_spec(requirement, spec, entities, season, date_range, population, goal.id),
                        requirement,
                    )
                )
            continue
        proposition = _from_requirement(requirement, entities, season, date_range, population, goal.id)
        if proposition is not None:
            propositions.append(_with_expectations(proposition, requirement))
    return validate_propositions(_refine_subjects(goal, propositions), context)


def _refine_subjects(goal, propositions):
    from app.semantic.subject_granularity import refine_subject_propositions
    from app.capabilities.analytics.conformance import independent_analytical_partitions
    return refine_subject_propositions(goal, propositions, independent_analytical_partitions(goal))


def ensure_propositions(goal: Goal, semantic=None, llm=None) -> list[AnswerProposition]:
    """Return the Goal's propositions, deriving and attaching them when absent."""
    if not goal.propositions:
        goal.propositions = build_propositions(goal, semantic, llm)
    else:
        goal.propositions = _refine_subjects(goal, goal.propositions)
    return goal.propositions


def _with_expectations(proposition: AnswerProposition, requirement: UserRequirement) -> AnswerProposition:
    """Copy the Requirement's information class and freeze the typed evidence expectations."""
    proposition.evidence_intent = (requirement.evidence_intent.model_copy(deep=True)
                                   if requirement.evidence_intent is not None else None)
    if requirement.relation_semantics is not None and proposition.relation_semantics is not None:
        # A decomposition proposal cannot mint a native-record predicate that the
        # owning Requirement never requested.
        proposition.relation_semantics = proposition.relation_semantics.model_copy(
            deep=True, update={'predicate': requirement.relation_semantics.predicate})
    resolved = getattr(requirement, "resolved_scope", None)
    info = str(getattr(resolved, "information_class", "") or "")
    if info:
        proposition.information_class = info
    proposition.temporal_expectation = derive_temporal_expectation(
        proposition, anchor_date=getattr(resolved, "anchor_date", None)
    )
    proposition.assertion_expectation = derive_assertion_expectation(proposition)
    proposition.authority_expectation = derive_authority_expectation(proposition)
    return proposition


def mandatory_propositions(goal: Goal) -> list[AnswerProposition]:
    return [p for p in ensure_propositions(goal) if p.mandatory]


def proposition_coverage_counts(propositions: list[AnswerProposition]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for proposition in propositions:
        counts[proposition.coverage.value] = counts.get(proposition.coverage.value, 0) + 1
    return counts


def all_mandatory_satisfied(propositions: list[AnswerProposition]) -> bool:
    mandatory = [p for p in propositions if p.mandatory]
    return bool(mandatory) and all(p.coverage == CoverageState.SATISFIED for p in mandatory)


def required_tokens(proposition: AnswerProposition) -> set[str]:
    """Content tokens of the required information, excluding generic question words.

    A relational proposition (cause/effect/quote/status/comparison/explanation) also carries
    the canonical relation cues from its ``RelationSemantics``, so a passage that establishes
    the relationship with a specific marker (e.g. "in response to ... proposed") can match the
    requested information without requiring the generic summary word "cause". Identity/scope
    propositions are unchanged.
    """
    tokens = _content_tokens(proposition.required_information)
    tokens |= _content_tokens(proposition.operation)
    tokens |= relation_cue_tokens(getattr(proposition, "relation_semantics", None))
    return tokens


def content_tokens(text: str) -> set[str]:
    """Stopword-filtered content tokens (shared with required-information matching)."""
    return _content_tokens(text)


def _from_requirement(
    requirement: UserRequirement,
    entities: list[str],
    season: int | None,
    date_range: Any,
    population: str | None,
    goal_id: str,
) -> AnswerProposition | None:
    value = requirement.value if isinstance(requirement.value, dict) else {}
    default_subject = entities[0] if entities else ""
    base = dict(
        goal_id=goal_id,
        requirement_id=requirement.id,
        statement=requirement.statement,
        mandatory=requirement.required,
        time_scope=_scope(season, date_range),
        population=population,
        relation_semantics=requirement.relation_semantics,
    )
    if requirement.kind == RequirementKind.ENTITY:
        mention = value.get("canonical_name") or value.get("mention") or requirement.statement
        return AnswerProposition(
            **base,
            kind=PropositionKind.IDENTITY,
            subject=str(mention),
            entity_id=str(value.get("canonical_id")) if value.get("canonical_id") else None,
            namespace=value.get("namespace"),
            required_information="identify the subject",
        )
    if requirement.kind == RequirementKind.SEASON:
        return AnswerProposition(
            **{**base, "time_scope": {"season": requirement.value}},
            kind=PropositionKind.SCOPE,
            subject=default_subject,
            required_information="pin the requested season",
        )
    if requirement.kind == RequirementKind.TIME:
        return AnswerProposition(
            **{**base, "time_scope": {"date_range": requirement.value}},
            kind=PropositionKind.SCOPE,
            subject=default_subject,
            required_information="pin the requested time window",
        )
    if requirement.kind == RequirementKind.GAME_POPULATION:
        return AnswerProposition(
            **{**base, "population": str(requirement.value)},
            kind=PropositionKind.SCOPE,
            subject=default_subject,
            required_information="pin the requested population",
        )
    if requirement.kind == RequirementKind.MEASURE:
        label = value.get("label") or requirement.statement
        return AnswerProposition(
            **base,
            kind=PropositionKind.VALUE,
            subject=default_subject,
            required_information=str(label),
        )
    if requirement.kind == RequirementKind.ANALYSIS:
        kind = (
            PropositionKind.COMPARISON if requirement.comparison is not None
            else PropositionKind.EXPLANATION
            if requirement.completion == RequirementCompletion.EXPLANATION
            else PropositionKind.GROUNDED
        )
        objective = value.get("objective") or requirement.statement
        return AnswerProposition(
            **base,
            kind=kind,
            subject=default_subject,
            required_information=str(objective),
            comparison=requirement.comparison.model_copy(deep=True) if requirement.comparison else None,
        )
    if requirement.kind == RequirementKind.EVIDENCE:
        objective = value.get("objective") or requirement.statement
        return AnswerProposition(
            **base,
            kind=PropositionKind.FACT,
            subject=default_subject,
            required_information=str(objective),
        )
    # CONSTRAINT and anything else: a grounded obligation that must still be addressed.
    return AnswerProposition(
        **base,
        kind=PropositionKind.GROUNDED,
        subject=default_subject,
        required_information=requirement.statement,
    )


def _explicit_specs(requirement: UserRequirement) -> list[dict[str, Any]]:
    if not isinstance(requirement.value, dict):
        return []
    specs = requirement.value.get("propositions")
    if not isinstance(specs, list):
        return []
    return [spec for spec in specs if isinstance(spec, dict) and spec.get("statement")]


def _from_spec(
    requirement: UserRequirement,
    spec: dict[str, Any],
    entities: list[str],
    season: int | None,
    date_range: Any,
    population: str | None,
    goal_id: str,
) -> AnswerProposition:
    try:
        kind = PropositionKind(str(spec.get("kind", "GROUNDED")).upper())
    except ValueError:
        kind = PropositionKind.GROUNDED
    time_scope = spec.get("time_scope")
    if not isinstance(time_scope, dict):
        time_scope = _scope_from_requirement(requirement, season, date_range)
    relation_semantics = spec.get("relation_semantics")
    if isinstance(relation_semantics, dict):
        try:
            relation_semantics = RelationSemantics.model_validate(relation_semantics)
        except Exception:  # noqa: BLE001 - malformed semantics falls back to the requirement
            relation_semantics = None
    if relation_semantics is None:
        relation_semantics = requirement.relation_semantics
    return AnswerProposition(
        goal_id=goal_id,
        requirement_id=requirement.id,
        statement=str(spec.get("statement")),
        kind=kind,
        subject=str(spec.get("subject") or (entities[0] if entities else "")),
        entity_id=str(spec["entity_id"]) if spec.get("entity_id") else None,
        time_scope=time_scope,
        population=str(spec.get("population") or population or "") or None,
        required_information=str(spec.get("required_information") or spec.get("statement")),
        operation=str(spec.get("operation") or ""),
        mandatory=requirement.required,
        depends_on=[str(d) for d in spec.get("depends_on", []) if d],
        relation_semantics=relation_semantics,
        comparison=(requirement.comparison.model_copy(deep=True)
                    if kind == PropositionKind.COMPARISON and requirement.comparison else None),
    )


def _entity_context(goal: Goal) -> list[str]:
    names: list[str] = []
    for requirement in goal.requirements:
        if requirement.kind != RequirementKind.ENTITY:
            continue
        value = requirement.value if isinstance(requirement.value, dict) else {}
        reference = requirement.entity_ref
        name = (
            (reference.canonical_name or reference.surface)
            if reference is not None
            else value.get("canonical_name") or value.get("mention")
        )
        if name:
            names.append(str(name))
    seen: set[str] = set()
    unique: list[str] = []
    for name in names:
        key = name.strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(name)
    return unique


def _time_context(goal: Goal) -> tuple[int | None, Any]:
    season: int | None = None
    date_range: Any = None
    for requirement in goal.requirements:
        if requirement.kind == RequirementKind.SEASON and isinstance(requirement.value, int):
            season = requirement.value
        elif requirement.kind == RequirementKind.TIME and requirement.value and date_range is None:
            date_range = requirement.value
    if season is None:
        resolved = goal.resolved_time_scope or {}
        if isinstance(resolved.get("season"), int):
            season = resolved["season"]
    return season, date_range


def _population_context(goal: Goal) -> str | None:
    for requirement in goal.requirements:
        if requirement.kind == RequirementKind.GAME_POPULATION and requirement.value:
            return str(requirement.value).upper()
    return None


def _scope(season: int | None, date_range: Any) -> dict[str, Any]:
    scope: dict[str, Any] = {}
    if season is not None:
        scope["season"] = season
    if date_range is not None:
        scope["date_range"] = date_range
    return scope


def _scope_from_requirement(requirement: UserRequirement, season: int | None, date_range: Any) -> dict[str, Any]:
    """The proposition's scope: the Goal window plus the requirement's resolved scope.

    The Requirement resolved its temporal meaning once (M2.5); the proposition carries that
    canonical value instead of each capability re-deriving a relative window.
    """
    scope = _scope(season, date_range)
    resolved = getattr(requirement, "resolved_scope", None)
    if resolved is not None:
        if resolved.kind == "SEASON" and resolved.season is not None:
            scope.setdefault("season", resolved.season)
        elif resolved.is_window():
            scope.setdefault("date_range", {"start": resolved.start, "end": resolved.end})
            scope["resolved_temporal"] = resolved.model_dump()
    return scope


def _content_tokens(text: str) -> set[str]:
    import re

    tokens: set[str] = set()
    for match in re.finditer(r"[0-9]+(?:\.[0-9]+)?|[a-z][a-z0-9'\-]+|[\u4e00-\u9fff]+", (text or "").lower()):
        token = match.group(0)
        if token in _STOPWORDS:
            continue
        if all("\u4e00" <= ch <= "\u9fff" for ch in token):
            if len(token) >= 2:
                tokens.add(token)
        elif len(token) >= 3:
            tokens.add(token)
    return tokens
