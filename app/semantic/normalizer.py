"""Semantic normalization: raw query -> AnalysisObjective[] (+ clarifications).

The Planner never re-parses raw user intent (D023). This stage resolves entities,
normalizes constraints and extracts objectives. Meaning ambiguity is surfaced as a
ClarificationRequest instead of being silently decided (D045).
"""

from collections.abc import Callable, Iterable
from datetime import date, timedelta
import re

from app.models.clarification import ClarificationRequest
from app.models.contracts import CategoryConstraint, Constraint, Entity, TimeRange
from app.models.entities import CanonicalEntity
from app.models.semantic import SemanticResult
from app.semantic.constraints import normalize_constraints
from app.semantic.entity_resolver import EntityDictionary, EntityResolver
from app.semantic.objective_extractor import ObjectiveExtractor

_NAMESPACE_FALLBACK = "LOCAL"


class SemanticNormalizer:
    def __init__(self, extractor: ObjectiveExtractor, entity_resolver: EntityResolver,
                 dictionary: EntityDictionary | None = None,
                 id_factory: Callable[[str], str] | None = None,
                 today: Callable[[], date] = date.today) -> None:
        self._extractor = extractor
        self._today = today
        self._resolver = entity_resolver
        self._dictionary = dictionary or EntityDictionary()
        self._id_factory = id_factory or (lambda prefix: f"{prefix}-{abs(hash(prefix))}")

    def normalize(self, raw_query: str, constraints: Iterable[Constraint] = (),
                  mentions: Iterable[str] | None = None) -> SemanticResult:
        supplied = tuple(constraints)
        inferred = ()
        # Freeze relative dates before any interaction or execution can suspend the run.
        if not any(c.key == "date_range" for c in supplied):
            recent = re.search(r"(?:近|过去|過去)\s*(\d+)\s*天|\blast\s+(\d+)\s+days\b",
                               raw_query, re.IGNORECASE)
            dates = re.findall(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)", raw_query)
            years = set(re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", raw_query))
            window = None
            if dates:
                if recent or len(dates) > 2:
                    raise ValueError("Ambiguous date windows require clarification")
                window = TimeRange(start=dates[0], end=dates[-1])
            elif years:
                if recent or len(years) != 1:
                    raise ValueError("Multiple date windows require clarification")
                year = int(next(iter(years)))
                window = TimeRange(start=date(year, 1, 1), end=date(year, 12, 31))
            if recent:
                days = int(recent.group(1) or recent.group(2))
                if not 1 <= days <= 36600:
                    raise ValueError("Recent date window must be between 1 and 36600 days")
                end = self._today()
                start = end - timedelta(days=days - 1)
                window = TimeRange(start=start, end=end)
            if window is not None:
                inferred = (CategoryConstraint(key="date_range",
                    values=(window.start.isoformat(), window.end.isoformat()),
                    origin="SYSTEM_INFERRED"),)
        normalized = normalize_constraints((*supplied, *inferred))
        surface_mentions = tuple(mentions) if mentions is not None else self._scan_mentions(raw_query)

        entities: list[Entity] = []
        clarifications: list[ClarificationRequest] = []
        unresolved: list[str] = []
        notes: list[str] = []
        for mention in surface_mentions:
            resolution = self._resolver.resolve(mention)
            if resolution.canonical is not None:
                entities.append(_to_entity(resolution.canonical))
                continue
            unresolved.append(mention)
            request = self._resolver.propose_clarification(resolution)
            if request is not None:
                clarifications.append(request)

        objectives = self._extractor.extract(raw_query, entities=tuple(entities),
                                             constraints=normalized)
        if clarifications:
            notes.append("Objectives are provisional until clarifications are answered.")
        return SemanticResult(raw_query=raw_query, objectives=tuple(objectives),
                              clarifications=tuple(clarifications),
                              unresolved_mentions=tuple(unresolved), notes=tuple(notes))

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
