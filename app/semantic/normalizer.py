"""Semantic normalization: raw query -> AnalysisObjective[] (+ clarifications).

The Planner never re-parses raw user intent (D023). This stage resolves entities,
normalizes constraints and extracts objectives. Meaning ambiguity is surfaced as a
ClarificationRequest instead of being silently decided (D045).
"""

from collections.abc import Callable, Iterable
from datetime import date, timedelta
import re

from app.models.clarification import ClarificationOption, ClarificationRequest
from app.models.contracts import CategoryConstraint, Constraint, Entity, TimeRange
from app.models.entities import CanonicalEntity
from app.models.semantic import SemanticResult
from app.semantic.analytics_intent import (extract_analytical_constraints,
                                           location_clarification_options)
from app.semantic.constraints import normalize_constraints
from app.semantic.entity_resolver import EntityDictionary, EntityResolver
from app.semantic.objective_extractor import ObjectiveExtractor

_NAMESPACE_FALLBACK = "LOCAL"


class SemanticNormalizer:
    def __init__(self, extractor: ObjectiveExtractor, entity_resolver: EntityResolver,
                 dictionary: EntityDictionary | None = None,
                 id_factory: Callable[[str], str] | None = None,
                 today: Callable[[], date] = date.today,
                 semantic_parser=None) -> None:
        self._extractor = extractor
        self._today = today
        self._resolver = entity_resolver
        self._dictionary = dictionary or EntityDictionary()
        self._id_factory = id_factory or (lambda prefix: f"{prefix}-{abs(hash(prefix))}")
        # Optional hybrid semantic parser. When present it is the single source of
        # analytical constraints, so deterministic and LLM extraction share one validated
        # path. When absent the deterministic parser is used directly (legacy seam).
        self._semantic_parser = semantic_parser

    def normalize(self, raw_query: str, constraints: Iterable[Constraint] = (),
                  mentions: Iterable[str] | None = None) -> SemanticResult:
        supplied = tuple(constraints)
        inferred = ()
        windows = self._date_windows(raw_query, supplied)
        semantic_failure = ""
        semantic_notes: list[str] = []
        if self._semantic_parser is not None:
            parsed = self._semantic_parser.parse(raw_query)
            analytics_constraints = parsed.constraints
            location_wording_requested = parsed.location_wording_requested
            semantic_failure = parsed.clarification_reason
            semantic_notes.append(
                f"semantic parser: {parsed.extractor} ({parsed.parser_version})")
            if parsed.summary:
                semantic_notes.append("semantic constraints: " + ",".join(parsed.summary))
            if parsed.ambiguities:
                semantic_notes.append(
                    "semantic ambiguities: " + ",".join(item.kind for item in parsed.ambiguities))
            if parsed.fallback_reason:
                semantic_notes.append(f"semantic fallback: {parsed.fallback_reason}")
            if parsed.clarification_reason:
                semantic_notes.append(
                    f"semantic validation: {parsed.clarification_reason}")
            review = parsed.review
            if review is not None:
                semantic_notes.append(
                    f"semantic review: outcome={review.outcome} "
                    f"agreement={review.agreement_status}")
                semantic_notes.append(
                    f"semantic models: extractor={review.extractor_model or '-'} "
                    f"reviewer={review.reviewer_model or '-'}")
                for call in review.calls:
                    semantic_notes.append(
                        f"semantic call: role={call.role} model={call.model or '-'} "
                        f"ok={call.ok} latency_ms={call.latency_ms}")
                for difference in review.material_differences:
                    semantic_notes.append(
                        f"semantic material difference: {difference.dimension}")
        else:
            analytics = extract_analytical_constraints(raw_query)
            analytics_constraints = analytics.constraints
            location_wording_requested = analytics.location_wording_requested
        surface_mentions = tuple(mentions) if mentions is not None else self._scan_mentions(raw_query)

        entities: list[Entity] = []
        clarifications: list[ClarificationRequest] = []
        unresolved: list[str] = []
        notes: list[str] = list(semantic_notes)
        for mention in surface_mentions:
            resolution = self._resolver.resolve(mention)
            if resolution.canonical is not None:
                entities.append(_to_entity(resolution.canonical))
                continue
            unresolved.append(mention)
            request = self._resolver.propose_clarification(resolution)
            if request is not None:
                clarifications.append(request)

        if semantic_failure:
            clarifications.append(ClarificationRequest(
                clarification_id=self._id_factory("clarification"), kind="MEANING",
                question="The analytics semantics of this request could not be resolved "
                         "safely. Please restate the metric, threshold and population "
                         "explicitly.",
                reason=f"semantic_{semantic_failure.casefold()}",
                affected_ref="analytics_semantics"))

        if location_wording_requested and not any(
                c.kind == "LOCATION" for c in (*supplied, *analytics_constraints)):
            options = tuple(
                ClarificationOption(option_id=f"loc-{index}", label=label, value=value,
                                    rationale=rationale)
                for index, (value, label, rationale) in enumerate(location_clarification_options()))
            clarifications.append(ClarificationRequest(
                clarification_id=self._id_factory("clarification"), kind="CONSTRAINT",
                question="Which upper-zone definition should the pitch-location filter use?",
                reason="'Near the upper edge of the strike zone' has several defensible "
                       "definitions, and the exact batter-relative one needs fields the "
                       "local archive may not provide.",
                options=options, recommended_option_id=options[0].option_id,
                affected_ref="pitch_location"))

        objectives = self._build_objectives(raw_query, supplied, analytics_constraints,
                                            entities, windows)
        if clarifications:
            notes.append("Objectives are provisional until clarifications are answered.")
        return SemanticResult(raw_query=raw_query, objectives=tuple(objectives),
                              clarifications=tuple(clarifications),
                              unresolved_mentions=tuple(unresolved), notes=tuple(notes))

    def _date_windows(self, raw_query: str, supplied: tuple[Constraint, ...]) -> tuple[TimeRange, ...]:
        """Freeze zero, one or two deterministic date windows.

        Explicit comparisons (``2023 vs 2024``, ``last 30 days vs previous 30 days``) yield
        two windows; a single window yields one; genuinely ambiguous multi-window text
        still fails closed.
        """
        if any(c.key == "date_range" for c in supplied):
            return ()
        lowered = raw_query.casefold()
        recent = re.search(r"(?:近|过去|過去)\s*(\d+)\s*天|\blast\s+(\d+)\s+days\b",
                           raw_query, re.IGNORECASE)
        dates = re.findall(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)", raw_query)
        years = sorted({int(y) for y in re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", raw_query)})
        comparison = re.search(r"\bvs\.?\b|\bversus\b|\bcompared\s+to\b", lowered)

        if comparison and len(years) == 2 and not recent and not dates:
            return (TimeRange(start=date(years[0], 1, 1), end=date(years[0], 12, 31)),
                    TimeRange(start=date(years[1], 1, 1), end=date(years[1], 12, 31)))
        if recent and (comparison or re.search(r"\bprevious\b|\bprior\b", lowered)):
            days = int(recent.group(1) or recent.group(2))
            if not 1 <= days <= 36600:
                raise ValueError("Recent date window must be between 1 and 36600 days")
            end = self._today()
            start = end - timedelta(days=days - 1)
            return (TimeRange(start=start, end=end),
                    TimeRange(start=start - timedelta(days=days), end=start - timedelta(days=1)))

        window: TimeRange | None = None
        if dates:
            if recent or len(dates) > 2:
                raise ValueError("Ambiguous date windows require clarification")
            window = TimeRange(start=dates[0], end=dates[-1])
        elif years:
            if recent or len(years) != 1:
                raise ValueError("Multiple date windows require clarification")
            window = TimeRange(start=date(years[0], 1, 1), end=date(years[0], 12, 31))
        if recent:
            days = int(recent.group(1) or recent.group(2))
            if not 1 <= days <= 36600:
                raise ValueError("Recent date window must be between 1 and 36600 days")
            end = self._today()
            start = end - timedelta(days=days - 1)
            window = TimeRange(start=start, end=end)
        return (window,) if window is not None else ()

    def _build_objectives(self, raw_query: str, supplied: tuple[Constraint, ...],
                          analytics: tuple[Constraint, ...], entities: list[Entity],
                          windows: tuple[TimeRange, ...]) -> list:
        if not windows:
            normalized = normalize_constraints((*supplied, *analytics))
            return list(self._extractor.extract(raw_query, entities=tuple(entities),
                                                constraints=normalized))
        objectives: list = []
        for window in windows:
            window_constraint = CategoryConstraint(
                key="date_range", values=(window.start.isoformat(), window.end.isoformat()),
                origin="SYSTEM_INFERRED")
            normalized = normalize_constraints((*supplied, window_constraint, *analytics))
            label = f"{window.start.isoformat()}..{window.end.isoformat()}"
            for objective in self._extractor.extract(raw_query, entities=tuple(entities),
                                                     constraints=normalized):
                objectives.append(objective.model_copy(update={
                    "objective_id": self._id_factory(f"objective-{label}"),
                    "description": f"{objective.description} ({label})",
                }))
        return objectives

    def _scan_mentions(self, raw_query: str) -> tuple[str, ...]:
        """Find known entity surfaces that literally occur in the query."""
        lowered = raw_query.casefold()
        matches: list[tuple[int, int, str]] = []
        for entity in self._dictionary.entities():
            surfaces = (entity.display_name, *entity.aliases)
            for surface in surfaces:
                min_length = 2 if re.search(r"[\u4e00-\u9fff]", surface) else 3
                if len(surface) < min_length:
                    continue
                pattern = r"(?<![a-z0-9])" + re.escape(surface.casefold()) + r"(?![a-z0-9])"
                for match in re.finditer(pattern, lowered):
                    matches.append((match.start(), match.end(), surface))
        selected: list[tuple[int, int, str]] = []
        for start, end, surface in sorted(matches, key=lambda item: (item[0] - item[1], item[0], item[2])):
            if not any(start < other_end and other_start < end for other_start, other_end, _ in selected):
                selected.append((start, end, surface))
        return tuple(dict.fromkeys(surface for _, _, surface in sorted(selected)))

    def entity_for_key(self, entity_key: str) -> CanonicalEntity:
        return self._dictionary.get(entity_key)


def _to_entity(canonical: CanonicalEntity) -> Entity:
    namespace, _, identifier = canonical.entity_key.partition(":")
    if not identifier:
        namespace, identifier = _NAMESPACE_FALLBACK, canonical.entity_key
    return Entity(namespace=namespace, entity_type=canonical.entity_type, identifier=identifier)
