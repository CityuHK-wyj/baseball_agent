"""Requirement/proposition-aware query planning (M2-2, ADR 0027).

The M1 query builder emitted at most three queries composed from the subject join, an
explicit query and a broad subject+objective string. A broad entity query such as
``Major League Baseball`` could therefore run before the relationship-specific strike/cause
query and consume the bounded fetch budget on generic pages.

This module builds a deterministic :class:`ResearchQueryPlan` whose facets map to the
mandatory propositions (and to their required information / scope), orders relationship
facets before the broad entity facet, and only then optionally asks the LLM for bounded
English phrasings. It is **not** a new Planner agent: it is a pure builder consumed inside
``web_research.execute``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.contracts.models import RelationKind
from app.semantic.relations import (
    CAUSE_QUERY_CUES,
    EFFECT_QUERY_CUES,
    HIGH_RISK_CUES,
    QUOTE_QUERY_CUES,
    is_high_risk_text,
    relation_kinds_from_text,
)
from app.verification.propositions import content_tokens

# Intents, in the order the plan should prefer them (specific relationship first).
INTENT_ORDER = ("INTERVIEW", "FRESHNESS", "METRIC", "EVENT", "RELATION", "MISSING", "ENTITY")

# Canonical relation kind -> query-plan intent. These are routing signals read from the
# Requirement's RelationSemantics, not hardcoded tool rules.
INTENT_FOR_RELATION: dict[RelationKind, str] = {
    RelationKind.QUOTE: "INTERVIEW",
    RelationKind.STATUS: "FRESHNESS",
    RelationKind.FRESHNESS: "FRESHNESS",
    RelationKind.VALUE: "METRIC",
    RelationKind.AGGREGATION: "METRIC",
    RelationKind.COMPARISON: "RELATION",
    RelationKind.CAUSE: "EVENT",
    RelationKind.EFFECT: "EVENT",
    RelationKind.EXPLANATION: "EVENT",
    RelationKind.EVENT: "EVENT",
}


@dataclass(frozen=True)
class QueryVariant:
    text: str
    intent: str
    target_proposition_ids: tuple[str, ...] = ()
    time_scope: dict[str, Any] = field(default_factory=dict)
    source_hint: tuple[str, ...] = ()
    language: str = "en"


@dataclass(frozen=True)
class ResearchQueryPlan:
    objective: str
    subjects: tuple[str, ...]
    aliases: tuple[str, ...]
    variants: tuple[QueryVariant, ...]
    freshness: str = "MIXED"
    generated_by: str = "deterministic"
    facets_missing: tuple[str, ...] = ()
    research_languages: tuple[str, ...] = ("en",)
    expansion_reasons: tuple[str, ...] = ()
    source_intent: str = "ANALYSIS"

    def as_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "subjects": list(self.subjects),
            "aliases": list(self.aliases),
            "freshness": self.freshness,
            "generated_by": self.generated_by,
            "facets_missing": list(self.facets_missing),
            "research_languages": list(self.research_languages),
            "expansion_reasons": list(self.expansion_reasons),
            "source_intent": self.source_intent,
            "variants": [
                {
                    "text": variant.text,
                    "intent": variant.intent,
                    "target_proposition_ids": list(variant.target_proposition_ids),
                    "time_scope": variant.time_scope,
                    "source_hint": list(variant.source_hint),
                    "language": variant.language,
                }
                for variant in self.variants
            ],
        }


def _prop_field(proposition: Any, key: str, default: Any = "") -> Any:
    if isinstance(proposition, dict):
        return proposition.get(key, default)
    return getattr(proposition, key, default)


def _prop_id(proposition: Any) -> str:
    return str(_prop_field(proposition, "id", ""))


def _prop_kind(proposition: Any) -> str:
    kind = _prop_field(proposition, "kind", "")
    return getattr(kind, "value", str(kind)).upper()


def _prop_relation_kind(proposition: Any) -> RelationKind | None:
    if proposition is None:
        return None
    relation = proposition.get("relation_semantics") if isinstance(proposition, dict) else getattr(proposition, "relation_semantics", None)
    if relation is None:
        return None
    kind = relation.get("relation_kind") if isinstance(relation, dict) else getattr(relation, "relation_kind", None)
    if kind is None:
        return None
    try:
        return RelationKind(getattr(kind, "value", str(kind)).upper())
    except ValueError:
        return None


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    low = (text or "").lower()
    return any(term in low for term in terms)


def _subject_terms(subjects: Iterable[str]) -> list[str]:
    return [str(subject).strip() for subject in subjects if str(subject).strip()]


def is_high_risk(text: str) -> bool:
    return is_high_risk_text(text)


def freshness_class(objective: str, propositions: Iterable[Any]) -> str:
    text = " ".join([objective, *[str(_prop_field(p, "statement", "")) for p in propositions]])
    years = [int(y) for y in __import__("re").findall(r"\b(19\d{2}|20\d{2})\b", text)]
    current = time.gmtime().tm_year
    if any(year <= current - 1 for year in years):
        return "IMMUTABLE"
    from app.semantic.relations import FRESHNESS_QUERY_CUES
    if _contains_any(text, FRESHNESS_QUERY_CUES) or _contains_any(text, HIGH_RISK_CUES) or any(year >= current for year in years):
        return "VOLATILE"
    return "MIXED"


def _intent_for(proposition: Any, objective: str, user_wording: str) -> str:
    kind = _prop_kind(proposition)
    relation_kind = _prop_relation_kind(proposition)
    if relation_kind is not None and relation_kind in INTENT_FOR_RELATION:
        return INTENT_FOR_RELATION[relation_kind]
    combined = f"{objective} {user_wording} {_prop_field(proposition, 'required_information', '')}"
    if _contains_any(combined, QUOTE_QUERY_CUES):
        return "INTERVIEW"
    if kind == "VALUE":
        return "METRIC"
    if kind == "FACT" and is_high_risk(combined):
        return "FRESHNESS"
    if kind in {"EXPLANATION", "EVENT"}:
        return "EVENT"
    return "RELATION"


def _with_scope(text: str, proposition: Any) -> str:
    time_scope = _prop_field(proposition, "time_scope", {}) or {}
    if isinstance(time_scope, dict) and time_scope.get("season"):
        text = f"{text} {time_scope['season']}"
    population = _prop_field(proposition, "population", "")
    if population:
        text = f"{text} {str(population).replace('_', ' ').lower()}"
    return " ".join(text.split())


def _relation_variant(subject: str, proposition: Any, objective: str, user_wording: str) -> QueryVariant:
    required = str(_prop_field(proposition, "required_information", "") or "").strip()
    intent = _intent_for(proposition, objective, user_wording)
    text = _with_scope(" ".join(part for part in (subject, required) if part), proposition)
    return QueryVariant(
        text=text,
        intent=intent,
        target_proposition_ids=(_prop_id(proposition),) if _prop_id(proposition) else (),
        time_scope=dict(_prop_field(proposition, "time_scope", {}) or {}),
    )


def build_query_plan(
    *,
    objective: str,
    subjects: Iterable[str],
    propositions: Iterable[Any] = (),
    user_wording: str = "",
    aliases: Iterable[str] = (),
    search_terms: Iterable[str] = (),
    explicit_query: str = "",
    target_information: str = "",
    research_languages: Iterable[str] = ("en",),
    expansion_reasons: Iterable[str] = (),
    source_intent: str = "ANALYSIS",
    max_variants: int = 6,
) -> ResearchQueryPlan:
    """Build a deterministic, specific-first query plan.

    Every variant is a :class:`QueryVariant`; the broad ENTITY facet (subject only) is always
    considered **after** every relationship facet, so it cannot consume the fetch budget before
    the mandatory relationship has had a chance to be retrieved.
    """
    subject_list = _subject_terms(subjects)
    alias_list = [str(alias).strip() for alias in aliases if str(alias).strip()]
    proposition_list = list(propositions)
    mandatory = [p for p in proposition_list if bool(_prop_field(p, "mandatory", False))] or proposition_list
    primary_subject = subject_list[0] if subject_list else ""
    subject_phrase = " ".join(subject_list)

    ordered: list[QueryVariant] = []
    covered: set[str] = set()

    # 1. One relationship facet per mandatory proposition, mapped to its id.
    for proposition in mandatory:
        if not primary_subject:
            break
        variant = _relation_variant(primary_subject, proposition, objective, user_wording)
        if variant.text and variant.text.lower() not in {item.text.lower() for item in ordered}:
            ordered.append(variant)
            covered.add(_prop_id(proposition))

    # 2. Explicit cause/effect facets when the Goal asks for them (canonical relation cues).
    combined_goal = f"{objective} {user_wording} {target_information}"
    goal_kinds = set(relation_kinds_from_text(combined_goal))
    if primary_subject and (RelationKind.CAUSE in goal_kinds or _contains_any(combined_goal, CAUSE_QUERY_CUES)):
        text = _with_scope(f"{primary_subject} causes reasons", mandatory[0] if mandatory else None)
        _append_unique(ordered, QueryVariant(text=text, intent="EVENT", target_proposition_ids=tuple(_prop_id(p) for p in mandatory)))
    if primary_subject and (RelationKind.EFFECT in goal_kinds or _contains_any(combined_goal, EFFECT_QUERY_CUES)):
        text = _with_scope(f"{primary_subject} effects impact consequences", mandatory[0] if mandatory else None)
        _append_unique(ordered, QueryVariant(text=text, intent="EVENT", target_proposition_ids=tuple(_prop_id(p) for p in mandatory)))

    # 3. Interview / freshness intent facets.
    if primary_subject and _contains_any(combined_goal, QUOTE_QUERY_CUES):
        _append_unique(ordered, QueryVariant(text=f"{primary_subject} interview quotes", intent="INTERVIEW"))
    if primary_subject and is_high_risk(combined_goal):
        current = time.gmtime().tm_year
        _append_unique(ordered, QueryVariant(text=f"{primary_subject} injury roster status {current}", intent="FRESHNESS"))

    # 4. Search terms supplied by the Planner, as a relationship facet.
    if subject_phrase and search_terms:
        _append_unique(ordered, QueryVariant(text=f"{subject_phrase} {' '.join(str(t) for t in search_terms)}", intent="RELATION"))

    # 5. Explicit Planner query.
    if explicit_query:
        _append_unique(ordered, QueryVariant(text=str(explicit_query), intent="RELATION"))

    # 6. Multilingual expansion facets (only when the policy triggered them).
    for language in [lang for lang in research_languages if lang != "en"]:
        if not (primary_subject or user_wording):
            break
        alias = next((name for name in alias_list), "")
        base = (user_wording or alias or primary_subject) if source_intent in {
            "COMMUNITY_DISCUSSION", "LOCAL_REPORTING", "INTERVIEW"
        } else (alias or user_wording or primary_subject)
        _append_unique(
            ordered,
            QueryVariant(
                text=f"{base} {target_information}".strip(),
                intent="RELATION",
                language=language,
            ),
        )

    # 7. The broad ENTITY facet is always last.
    if subject_phrase:
        _append_unique(ordered, QueryVariant(text=subject_phrase, intent="ENTITY"))

    if not ordered:
        ordered.append(QueryVariant(text=objective or user_wording, intent="MISSING"))

    # Bound the plan; specific facets are kept ahead of the broad entity facet.
    if tuple(research_languages)[:1] != ("en",):
        ordered.sort(key=lambda variant: 0 if variant.language != "en" else 1)
    ordered = ordered[:max_variants]
    facets_missing = tuple(
        _prop_id(p) for p in mandatory if _prop_id(p) and _prop_id(p) not in covered
    )
    return ResearchQueryPlan(
        objective=objective,
        subjects=tuple(subject_list),
        aliases=tuple(alias_list),
        variants=tuple(ordered),
        freshness=freshness_class(objective, proposition_list),
        generated_by="deterministic",
        facets_missing=facets_missing,
        research_languages=tuple(research_languages),
        expansion_reasons=tuple(expansion_reasons),
        source_intent=source_intent,
    )


def _append_unique(variants: list[QueryVariant], variant: QueryVariant) -> None:
    if not variant.text:
        return
    normalized = " ".join(variant.text.split()).lower()
    if any(" ".join(item.text.split()).lower() == normalized for item in variants):
        return
    variants.append(variant)


def merge_llm_variants(plan: ResearchQueryPlan, generated: Iterable[str], *, language: str = "en") -> ResearchQueryPlan:
    """Append bounded LLM phrasings without letting them displace the deterministic facets.

    The LLM may only add variants; it may not reorder the specific facets ahead of the broad
    entity facet. Variants are added after the deterministic ones and de-duplicated.
    """
    variants = list(plan.variants)
    for text in generated:
        text = str(text or "").strip()
        if not text:
            continue
        _append_unique(variants, QueryVariant(text=text, intent="RELATION", language=language, source_hint=("llm",)))
    return ResearchQueryPlan(
        objective=plan.objective,
        subjects=plan.subjects,
        aliases=plan.aliases,
        variants=tuple(variants),
        freshness=plan.freshness,
        generated_by="deterministic+llm",
        facets_missing=plan.facets_missing,
        research_languages=plan.research_languages,
        expansion_reasons=plan.expansion_reasons,
        source_intent=plan.source_intent,
    )


def plan_texts(plan: ResearchQueryPlan) -> list[str]:
    return [variant.text for variant in plan.variants]


def variant_for_text(plan: ResearchQueryPlan, text: str) -> QueryVariant | None:
    for variant in plan.variants:
        if variant.text == text:
            return variant
    return None
