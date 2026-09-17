"""Narrow deterministic lexical anchors.

Anchors are the *supporting evidence* layer of the dual semantic architecture. They are
deliberately small: high-confidence facts that deterministic code can prove from fixed
vocabulary (explicit years, numeric literals with units, count literals, ranking limits,
population wording, known pitch families and explicit location definitions). They are not
a natural-language parser and must never be treated as the complete meaning of a query.

Their only job is to let the deterministic validator answer two bounded questions about a
proposed candidate:

* does a proposed explicit value *contradict* a lexical fact the user actually wrote?
* did the candidate *drop* an explicit restriction the user actually wrote?

A query-level conflict between anchors (for example ``top 5 or top 10``) is itself an
ambiguity and is surfaced rather than silently resolved.
"""

import re
from dataclasses import dataclass

from app.models.contracts import (Constraint, CountConstraint, LocationConstraint,
                                  NumericConstraint, PitchTypeConstraint,
                                  PopulationConstraint, RankingConstraint)
from app.semantic import analytics_intent as _ai


@dataclass(frozen=True)
class RankingLimitAnchor:
    """One ``top N`` / ``bottom N`` / rank-verb limit found in the query."""

    direction: str
    limit: int
    explicit: bool
    text: str


@dataclass(frozen=True)
class PopulationAnchor:
    """Independent population wording: ``None`` means the dimension was not stated."""

    game_types: tuple[str, ...] | None = None
    event_population: str | None = None
    text: str = ""


@dataclass(frozen=True)
class QualificationPhraseAnchor:
    """A qualification phrase. ``value`` is ``None`` for spelled numbers we do not parse.

    Spelled numbers are intentionally *detected but not converted*: the deterministic
    high-confidence path must not silently guess a value it cannot prove, so a provider
    failure over such a phrase clarifies instead of substituting a default.
    """

    text: str
    value: int | None


@dataclass(frozen=True)
class LexicalAnchors:
    """The bounded lexical facts of one raw query."""

    constraints: tuple[Constraint, ...] = ()
    ranking_limits: tuple[RankingLimitAnchor, ...] = ()
    qualification_phrases: tuple[QualificationPhraseAnchor, ...] = ()
    population: PopulationAnchor | None = None
    location_ambiguity: bool = False
    has_analytical_cue: bool = False

    def numerics(self) -> tuple[NumericConstraint, ...]:
        return tuple(item for item in self.constraints if isinstance(item, NumericConstraint))

    def counts(self) -> tuple[CountConstraint, ...]:
        return tuple(item for item in self.constraints if isinstance(item, CountConstraint))

    def rankings(self) -> tuple[RankingConstraint, ...]:
        return tuple(item for item in self.constraints if isinstance(item, RankingConstraint))

    def populations(self) -> tuple[PopulationConstraint, ...]:
        return tuple(item for item in self.constraints if isinstance(item, PopulationConstraint))

    def pitch_families(self) -> tuple[str, ...]:
        return tuple(sorted({item.family for item in self.constraints
                             if isinstance(item, PitchTypeConstraint)}))

    def locations(self) -> tuple[str, ...]:
        return tuple(sorted({item.definition for item in self.constraints
                             if isinstance(item, LocationConstraint)}))


# Spelled-out qualification numbers. The value is deliberately not converted; only the
# presence of an explicit restriction is proven, which is enough to refuse a silent drop.
_NUMBER_WORD = (r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
                r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|"
                r"thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|"
                r"dozen)")
_SPELLED_QUALIFICATION_RE = re.compile(
    r"(?:\b(?:minimum|min|at\s+least|no\s+less\s+than)\s+(?:of\s+)?)\s*"
    r"(?P<value>" + _NUMBER_WORD + r"(?:[\s-]+" + _NUMBER_WORD + r"){0,4})\s*"
    r"(?P<unit>batted[-\s]?balls?|batted[-\s]?ball\s+events?|bbe|balls\s+in\s+play|"
    r"qualifying\s+events?|qualifying\s+batted\s+balls?)\b",
    re.IGNORECASE)


def _ranking_limits(raw_query: str) -> tuple[RankingLimitAnchor, ...]:
    anchors: list[RankingLimitAnchor] = []
    for match in _ai._RANKING_RE.finditer(raw_query):
        direction = "ASC" if match.group("dir").casefold() == "bottom" else "DESC"
        anchors.append(RankingLimitAnchor(direction=direction, limit=int(match.group("limit")),
                                          explicit=True, text=match.group(0)))
    if not anchors:
        verb = _ai._RANK_VERB_RE.search(raw_query)
        if verb is not None:
            anchors.append(RankingLimitAnchor(direction="DESC",
                                              limit=_ai.DEFAULT_RANKING_LIMIT,
                                              explicit=False, text=verb.group(0)))
    return tuple(anchors)


def _qualification_phrases(raw_query: str) -> tuple[QualificationPhraseAnchor, ...]:
    phrases: list[QualificationPhraseAnchor] = []
    for match in _ai._QUALIFICATION_RE.finditer(raw_query):
        phrases.append(QualificationPhraseAnchor(text=match.group(0),
                                                 value=int(match.group("value"))))
    for match in _SPELLED_QUALIFICATION_RE.finditer(raw_query):
        phrases.append(QualificationPhraseAnchor(text=match.group(0), value=None))
    seen: set[str] = set()
    unique: list[QualificationPhraseAnchor] = []
    for phrase in phrases:
        key = phrase.text.casefold().strip()
        if key in seen:
            continue
        seen.add(key)
        unique.append(phrase)
    return tuple(unique)


def _population_anchor(raw_query: str) -> PopulationAnchor | None:
    """Detect population wording independently of any other analytical constraint."""
    game_types: tuple[str, ...] | None = None
    if any(re.search(cue, raw_query, re.IGNORECASE) for cue in _ai._POSTSEASON_CUES):
        game_types = ("POSTSEASON",)
    elif any(re.search(cue, raw_query, re.IGNORECASE) for cue in _ai._SPRING_TRAINING_CUES):
        game_types = ("SPRING_TRAINING",)
    elif any(re.search(cue, raw_query, re.IGNORECASE) for cue in _ai._EXHIBITION_CUES):
        game_types = ("EXHIBITION",)
    elif any(re.search(cue, raw_query, re.IGNORECASE) for cue in _ai._ALL_GAMES_CUES):
        game_types = _ai._ALL_GAME_TYPES
    elif any(re.search(cue, raw_query, re.IGNORECASE) for cue in _ai._REGULAR_SEASON_CUES):
        game_types = ("REGULAR_SEASON",)
    event: str | None = None
    if any(re.search(cue, raw_query, re.IGNORECASE) for cue in _ai._MEASURED_CONTACT_CUES):
        event = "MEASURED_CONTACT"
    elif any(re.search(cue, raw_query, re.IGNORECASE) for cue in _ai._ALL_PITCHES_CUES):
        event = "ALL_PITCHES"
    elif any(re.search(cue, raw_query, re.IGNORECASE) for cue in _ai._BATTED_BALL_CUES):
        event = "BATTED_BALL"
    if game_types is None and event is None:
        return None
    return PopulationAnchor(game_types=game_types, event_population=event, text=raw_query)


# Chinese/bilingual analytical cues. The deterministic parser cannot prove typed
# constraints from these, but their presence means the request is analytical and must be
# routed through the open-world (LLM-assisted) semantic path rather than silently treated
# as a plain knowledge question. High-confidence literal facts remain the only anchors.
_CJK_ANALYTICAL_CUES: tuple[str, ...] = (
    "快速球", "高区", "高區", "球速", "出速", "本垒打", "全壘打", "全垒打", "三振",
    "保送", "打击率", "打擊率", "上垒率", "上壘率", "长打率", "長打率", "打点", "打點",
    "打得", "打的", "更好", "最擅长", "最擅長", "变化", "變化", "面对", "面對",
    "表现", "表現", "战绩", "戰績", "球员", "球員",
)


def extract_lexical_anchors(raw_query: str) -> LexicalAnchors:
    """Extract the bounded lexical facts of ``raw_query``. Never raises."""
    intent = _ai.extract_analytical_constraints(raw_query)
    location_ambiguity = any(re.search(cue, raw_query, re.IGNORECASE)
                             for cue in _ai._AMBIGUOUS_LOCATION_CUES)
    population = _population_anchor(raw_query)
    cjk_cue = any(cue in raw_query for cue in _CJK_ANALYTICAL_CUES)
    has_cue = bool(intent.constraints) or location_ambiguity or population is not None or cjk_cue
    return LexicalAnchors(
        constraints=tuple(intent.constraints),
        ranking_limits=_ranking_limits(raw_query),
        qualification_phrases=_qualification_phrases(raw_query),
        population=population,
        location_ambiguity=location_ambiguity,
        has_analytical_cue=has_cue,
    )



