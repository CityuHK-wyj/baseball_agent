"""Semantic normalization: raw query -> AnalysisObjective[] (+ clarifications).

The Planner never re-parses raw user intent (D023); this stage resolves entities,
normalizes constraints and extracts objectives. In the v0.2 open-world runtime the stage
is *permissive*: it produces a partially structured ``SemanticUnderstanding`` alongside
the canonical typed constraints. Unknown entities, unknown concepts and open-ended
temporal wording are routing information, never terminal failures. Only genuinely
material ambiguity that cannot be recovered by an assumption or another tool becomes a
clarification.

Temporal language is interpreted here (not by a fragile regex that raised exceptions):
``2023和2025``, ``2023 vs 2024``, ``2023与2025``, ``from 2023 to 2025`` and
``今年和去年`` all produce comparison windows. A user query must never escape the CLI as
an uncaught parser/semantic exception.
"""

from collections.abc import Callable, Iterable
from datetime import date, timedelta
import re

from app.models.clarification import ClarificationOption, ClarificationRequest
from app.models.contracts import CategoryConstraint, Constraint, Entity, TimeRange
from app.models.entities import CanonicalEntity
from app.models.semantic import SemanticResult
from app.models.understanding import SemanticUnderstanding, detect_analysis_strategy
from app.semantic.analytics_intent import (extract_analytical_constraints,
                                           location_clarification_options)
from app.semantic.constraints import normalize_constraints
from app.semantic.entity_resolver import EntityDictionary, EntityResolver
from app.semantic.objective_extractor import ObjectiveExtractor

_NAMESPACE_FALLBACK = "LOCAL"

# -- temporal interpretation -------------------------------------------------

_COMPARISON_MARKERS: tuple[str, ...] = (
    "和", "与", "與", "vs", "v.s.", "versus", "compared with", "compared to",
    "对比", "對比", "比较", "比較", " between ",
)
_RANGE_MARKERS: tuple[tuple[str, str], ...] = (
    ("从", "到"), ("從", "到"), ("from", "to"), ("between", "and"),
)
_RELATIVE_YEARS: dict[str, int] = {"今年": 0, "去年": -1, "前年": -2, "明年": 1,
                                   "this year": 0, "last year": -1, "next year": 1}


def _comparison_requested(lowered: str) -> bool:
    return any(marker in lowered for marker in _COMPARISON_MARKERS)


def _strong_comparison(lowered: str) -> bool:
    """A comparison cue that is unambiguously temporal.

    The Chinese conjunction ``和`` can join two *entities* (``Ohtani和Judge``) rather than
    two time windows, so it is only a temporal cue when combined with an explicit
    comparison word or a second relative-year expression.
    """
    return any(marker in lowered for marker in
               ("vs", "v.s.", "versus", "compared with", "compared to", "对比", "對比",
                "比较", "比較", " between ")) or bool(
        re.search(r"和\s*(?:去年|前年|今年)|(?:去年|前年|今年)\s*和", lowered))


def _years_in(text: str) -> tuple[int, ...]:
    return tuple(sorted({int(year) for year in
                         re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", text)}))


def _dates_in(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)", text))


def _recent_days(raw_query: str) -> int | None:
    match = re.search(r"(?:近|过去|過去|最近)\s*(\d+)\s*天|(?:\blast|past|previous)\s+(\d+)\s+days\b",
                      raw_query, re.IGNORECASE)
    if match is None:
        return None
    value = int(match.group(1) or match.group(2))
    if not 1 <= value <= 36600:
        return None
    return value


class SemanticNormalizer:
    def __init__(self, extractor: ObjectiveExtractor, entity_resolver: EntityResolver,
                 dictionary: EntityDictionary | None = None,
                 id_factory: Callable[[str], str] | None = None,
                 today: Callable[[], date] = date.today,
                 semantic_parser=None, entity_recovery=None, web_recovery_available: bool = False) -> None:
        self._extractor = extractor
        self._today = today
        self._resolver = entity_resolver
        self._dictionary = dictionary or EntityDictionary()
        self._id_factory = id_factory or (lambda prefix: f"{prefix}-{abs(hash(prefix))}")
        # Optional open-world entity recovery (local + shared knowledge + web).
        self._entity_recovery = entity_recovery
        self._web_recovery_available = web_recovery_available
        # Optional hybrid semantic parser. When present it is the single source of
        # analytical constraints, so deterministic and LLM extraction share one validated
        # path. When absent the deterministic parser is used directly (legacy seam).
        self._semantic_parser = semantic_parser

    def normalize(self, raw_query: str, constraints: Iterable[Constraint] = (),
                  mentions: Iterable[str] | None = None) -> SemanticResult:
        supplied = tuple(constraints)
        windows = self._date_windows(raw_query, supplied)
        semantic_failure = ""
        semantic_notes: list[str] = []
        parsed_understanding: SemanticUnderstanding | None = None
        if self._semantic_parser is not None:
            parsed = self._semantic_parser.parse(raw_query)
            analytics_constraints = parsed.constraints
            location_wording_requested = parsed.location_wording_requested
            semantic_failure = parsed.clarification_reason
            parsed_understanding = parsed.understanding
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

        known_mentions = tuple(mentions) if mentions is not None else self._scan_mentions(raw_query)
        candidate_mentions = (self._detect_candidate_mentions(raw_query)
                              if mentions is None else ())

        entities: list[Entity] = []
        clarifications: list[ClarificationRequest] = []
        unresolved: list[str] = []
        notes: list[str] = list(semantic_notes)
        for mention in known_mentions:
            resolution = self._resolver.resolve(mention)
            if resolution.canonical is not None:
                entities.append(_to_entity(resolution.canonical))
                continue
            unresolved.append(mention)
            request = self._resolver.propose_clarification(resolution)
            if request is not None:
                clarifications.append(request)

        # Open-world candidate mentions are never silently dropped. A locally unresolved
        # mention becomes an explicit unresolved concept plus search hints so the Planner
        # can route entity resolution / web research. Only *materially ambiguous* local
        # candidates (more than one equally plausible match) force a clarification.
        unresolved_concepts: list[str] = []
        search_hints: list[str] = []
        web_available = ((self._entity_recovery is not None
                          and getattr(self._entity_recovery, "web_available", False))
                         or self._web_recovery_available)
        for mention in candidate_mentions:
            resolution = self._resolver.resolve(mention)
            if resolution.canonical is not None:
                entities.append(_to_entity(resolution.canonical))
                continue
            recovered = self._entity_recovery.recover(mention) if self._entity_recovery else None
            if recovered is not None and recovered.recovered and recovered.canonical is not None:
                entities.append(_to_entity(recovered.canonical))
                notes.append(
                    f"entity recovery: {mention} -> {recovered.canonical.entity_key} "
                    f"({recovered.reason})")
                continue
            candidates = recovered.candidates if recovered is not None else resolution.candidates
            if len({item.entity.entity_key for item in candidates}) > 1:
                ambiguous = resolution.model_copy(update={
                    "candidates": candidates, "needs_clarification": True,
                    "reason": (recovered.reason if recovered is not None else "") or resolution.reason})
                request = self._resolver.propose_clarification(ambiguous)
                if request is not None:
                    clarifications.append(request)
                continue
            # Unknown is routing information, not a silent drop: retain the mention and
            # ask the user only when no recovery path (web/entity resolution) exists.
            unresolved_concepts.append(mention)
            search_hints.extend(_search_hints_for(mention))
            if not web_available:
                clarifications.append(ClarificationRequest(
                    clarification_id=self._id_factory("clarification"), kind="ENTITY",
                    question=f"I could not identify '{mention}'. Which player or team do you mean?",
                    reason="The mention is not in the local dictionary and web entity "
                           "resolution is not configured for this run.",
                    affected_ref=mention))

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

        understanding = self._build_understanding(
            raw_query, parsed_understanding, tuple(entities), analytics_constraints,
            candidate_mentions, unresolved_concepts, search_hints, windows)
        if understanding is not None:
            notes.extend(understanding.summary())
        return SemanticResult(raw_query=raw_query, objectives=tuple(objectives),
                              clarifications=tuple(clarifications),
                              unresolved_mentions=tuple(unresolved), notes=tuple(notes),
                              understanding=understanding)

    # -- temporal interpretation -------------------------------------------
    def _date_windows(self, raw_query: str, supplied: tuple[Constraint, ...]
                      ) -> tuple[TimeRange, ...]:
        """Freeze zero, one or more deterministic date windows.

        Never raises: open-ended or multi-window temporal language is interpreted into
        per-window objectives (a comparison), not an uncaught exception. Exact literal
        handling stays deterministic; genuinely open relations are recorded as
        assumptions by the caller.
        """
        if any(c.key == "date_range" for c in supplied):
            return ()
        lowered = raw_query.casefold()
        dates = tuple(sorted(_dates_in(raw_query)))
        years = list(_years_in(raw_query))
        today = self._today()
        for surface, offset in _RELATIVE_YEARS.items():
            if surface in lowered:
                years.append(today.year + offset)
        years = sorted(set(years))
        recent = _recent_days(raw_query)
        comparison = _comparison_requested(lowered)
        strong_comparison = _strong_comparison(lowered)

        if recent is not None:
            end = today
            start = end - timedelta(days=recent - 1)
            if strong_comparison or re.search(r"\bprevious\b|\bprior\b|上一", lowered):
                return (TimeRange(start=start, end=end),
                        TimeRange(start=start - timedelta(days=recent), end=start - timedelta(days=1)))
            return (TimeRange(start=start, end=end),)

        if dates:
            if comparison and len(dates) >= 2:
                return tuple(TimeRange(start=_as_date(item), end=_as_date(item)) for item in dates[:4])
            return (TimeRange(start=_as_date(dates[0]), end=_as_date(dates[-1])),)

        if years:
            # Two-or-more apparent seasons become a comparison. `from 2023 to 2025` and
            # `2023和2025` both fall here; a bare year list is treated the same way rather
            # than raising.
            return tuple(TimeRange(start=date(year, 1, 1), end=date(year, 12, 31))
                         for year in years[:4])

        # Relative vocabulary without a numeric year (for example 去年 / last year).
        relative = [today.year + offset for surface, offset in _RELATIVE_YEARS.items()
                    if surface in lowered and not re.search(r"\d{4}", surface)]
        if relative:
            return tuple(TimeRange(start=date(year, 1, 1), end=date(year, 12, 31))
                         for year in sorted(set(relative))[:4])
        return ()

    # -- objective building -------------------------------------------------
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

    # -- open-world understanding ------------------------------------------
    def _build_understanding(self, raw_query: str,
                             parsed: SemanticUnderstanding | None,
                             entities: tuple[Entity, ...],
                             analytics: tuple[Constraint, ...],
                             candidate_mentions: tuple[str, ...],
                             unresolved_concepts: list[str],
                             search_hints: list[str],
                             windows: tuple[TimeRange, ...]) -> SemanticUnderstanding:
        strategy = detect_analysis_strategy(raw_query)
        time_hints = tuple(str(item) for item in
                           (*_dates_in(raw_query), *_years_in(raw_query)))
        base = parsed if parsed is not None else SemanticUnderstanding(raw_query=raw_query)
        merged_unresolved = tuple(dict.fromkeys((*base.unresolved_concepts, *unresolved_concepts)))
        merged_hints = tuple(dict.fromkeys((*base.search_hints, *search_hints)))
        recovery: list[str] = []
        if candidate_mentions or merged_unresolved:
            recovery.append("UNKNOWN_ENTITY")
        if strategy and not analytics:
            recovery.append("UNKNOWN_METRIC")
        return base.model_copy(update={
            "user_goal": base.user_goal or raw_query,
            "semantic_brief": base.semantic_brief
            or " | ".join(filter(None, (raw_query, strategy))) or raw_query,
            "analysis_strategy": base.analysis_strategy or strategy,
            "entities": tuple(dict.fromkeys((*base.entities, *entities))),
            "candidate_entity_mentions": tuple(dict.fromkeys(
                (*base.candidate_entity_mentions, *candidate_mentions))),
            "time_hints": tuple(dict.fromkeys((*base.time_hints, *time_hints))),
            "known_constraints": tuple(dict.fromkeys((*base.known_constraints, *analytics))),
            "unresolved_concepts": merged_unresolved,
            "search_hints": merged_hints,
            "recovery_codes": tuple(dict.fromkeys((*base.recovery_codes, *recovery))),
        })

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

    def _detect_candidate_mentions(self, raw_query: str) -> tuple[str, ...]:
        """Detect likely entity/nickname mentions not present in the local dictionary.

        This is intentionally a conservative heuristic, not a parser: it exists so an
        unknown name-like mention (for example ``太鼓达人``) is routed to entity
        resolution / web research instead of being silently dropped.
        """
        known = {surface.casefold() for entity in self._dictionary.entities()
                 for surface in (entity.display_name, *entity.aliases)}
        found: list[str] = []
        for mention in (*_cjk_candidates(raw_query), *_latin_candidates(raw_query)):
            lowered = mention.casefold()
            if lowered in known or any(lowered in surface or surface in lowered for surface in known):
                continue
            if mention not in found:
                found.append(mention)
        return tuple(found)

    def entity_for_key(self, entity_key: str) -> CanonicalEntity:
        return self._dictionary.get(entity_key)


# -- open-world mention heuristics ------------------------------------------

_CJK_FUNCTION_WORDS: tuple[str, ...] = (
    "今年", "去年", "前年", "明年", "最近", "目前", "现在", "现時", "赛季", "賽季",
    "战绩", "戰績", "表现", "表現", "状态", "狀態", "数据", "數據", "统计", "統計",
    "多少", "如何", "怎么", "怎麼", "怎么样", "怎麼樣", "为什么", "為什麼", "什么", "什麼",
    "哪个", "哪個", "谁是", "誰是", "变化", "變化", "擅长", "擅長", "处理", "處理",
    "打者", "投手", "球员", "球員", "球队", "球隊", "高区", "高區", "快速球", "速球",
    "快速", "本垒打", "全壘打", "全垒打", "三振", "保送", "打击", "打擊", "打点", "打點",
    "上垒", "上壘", "长打", "長打", "今天", "明天", "昨天", "一个月", "一個月",
    "面对", "面對", "意思", "意义", "意義", "含义", "含義", "打得", "打的", "突然",
    "变强", "變強", "老输", "老輸", "危险", "危險", "本地字典", "本地", "字典",
    "完全未知", "完全", "未知", "新秀", "伤病", "傷病", "伤停", "情況", "情况",
    "属于", "屬於", "分区", "分區", "联盟", "聯盟",
    "的", "了", "和", "与", "與", "是", "在", "有", "吗", "嗎", "呢", "吧", "啊",
    "谁", "誰", "最", "更", "里", "裡", "中", "上", "下", "之", "其", "请", "請",
    "我", "你", "他", "她", "它", "们", "們", "个", "個", "这", "這", "那", "为",
)
_CJK_STOP = frozenset(_CJK_FUNCTION_WORDS)

_LATIN_STOPWORDS = frozenset({
    "who", "what", "how", "which", "when", "where", "why", "the", "a", "an", "and",
    "or", "of", "in", "on", "for", "to", "from", "vs", "versus", "compare", "compared",
    "top", "bottom", "rank", "ranked", "highest", "lowest", "average", "max", "maximum",
    "min", "minimum", "exit", "velocity", "velo", "ev", "pitch", "pitches", "fastball",
    "fastballs", "ops", "obp", "slg", "hr", "bb", "k", "wrc", "war", "era", "whip",
    "recent", "last", "days", "season", "performance", "better", "best", "hit", "hits",
    "home", "run", "runs", "player", "players", "team", "teams", "league", "hitter",
    "hitters", "pitcher", "pitchers", "state", "form", "why", "did", "has", "have",
    "find", "show", "list", "get", "give", "tell", "during", "after", "before",
    "located", "while", "using", "use", "all", "any", "each", "every", "into", "over",
    "under", "please", "i", "me", "my", "who has been", "how has",
})


def _cjk_candidates(raw_query: str) -> tuple[str, ...]:
    """Extract likely CJK name-like chunks after removing known query vocabulary."""
    found: list[str] = []
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", raw_query):
        remaining = run
        for word in sorted(_CJK_STOP, key=len, reverse=True):
            remaining = remaining.replace(word, " ")
        for chunk in remaining.split():
            chunk = chunk.strip()
            if 2 <= len(chunk) <= 8:
                found.append(chunk)
    return tuple(dict.fromkeys(found))


def _latin_candidates(raw_query: str) -> tuple[str, ...]:
    """Extract capitalized Latin name-like sequences (``Freddie Freeman``, ``Ohtani``).

    Only runs of capitalized words separated by whitespace are joined, so ``Ohtani和Judge``
    yields two separate candidates rather than one merged sequence.
    """
    sequence = re.compile(
        r"[A-Z][A-Za-z'’À-ÖØ-öø-ÿ.\-]+(?:\s+[A-Z][A-Za-z'’À-ÖØ-öø-ÿ.\-]+)*")
    found: list[str] = []
    for match in sequence.finditer(raw_query):
        words = match.group(0).split()
        if any(word.strip(".'’-").casefold() in _LATIN_STOPWORDS for word in words):
            continue
        # All-caps acronyms (MLB, DFA, EV, OPS) are terms/metrics, not entity names.
        if all(word.strip(".'’-").isupper() for word in words):
            continue
        if not all(len(word.strip(".'’-")) >= 3 for word in words):
            continue
        found.append(" ".join(word.strip(".'’-") for word in words))
    return tuple(dict.fromkeys(found))


def _search_hints_for(mention: str) -> list[str]:
    hints = [f"{mention} MLB"]
    if re.search(r"[\u4e00-\u9fff]", mention):
        hints.append(f"{mention} 棒球")
    return hints


def _as_date(value: str) -> date:
    return date.fromisoformat(value)


def _to_entity(canonical: CanonicalEntity) -> Entity:
    namespace, _, identifier = canonical.entity_key.partition(":")
    if not identifier:
        namespace, identifier = _NAMESPACE_FALLBACK, canonical.entity_key
    return Entity(namespace=namespace, entity_type=canonical.entity_type, identifier=identifier)
