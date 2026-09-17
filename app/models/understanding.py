"""Open-world semantic understanding.

The v0.1 runtime forced every user request into a closed, fully typed
``SemanticCandidate`` before planning could start. Real-user dogfooding showed that this
prematurely rejects ordinary natural language ("太鼓达人今年战绩如何？",
"最近30天Ohtani和Judge谁打得更好？") because not every meaning has a typed enum yet.

This module introduces a permissive, partially structured representation that travels
alongside the strict typed constraints:

* high-confidence structured facts stay typed (entities, time hints, constraints);
* free-form fields (``user_goal``, ``semantic_brief``, ``planner_notes``,
  ``analysis_strategy``) carry arbitrary natural-language meaning;
* uncertainties and unknowns are first-class (``unresolved_concepts``,
  ``ambiguities``, ``recovery_codes``) so ``UNKNOWN != FAILED`` is represented in data.

The open-world understanding is an *interpretation aid*. It is never executable. Only the
strict SQL boundary (``app.models.sql_request``) may reach a privileged deterministic
database operation.
"""

from typing import Literal

from pydantic import Field

from app.models.contracts import Constraint, Contract, Entity

# Free-form fields are bounded to a generous runtime limit rather than reduced to enums.
MAX_FREE_TEXT = 20000
MAX_LIST_ITEMS = 64
MAX_LIST_ITEM = 500

# Recovery codes are routing information, not failures. A code tells the Planner which
# recovery path is worth trying (entity resolution, shared knowledge, web, re-plan).
RecoveryCode = Literal[
    "UNKNOWN_ENTITY",
    "UNKNOWN_NICKNAME",
    "UNKNOWN_METRIC",
    "UNKNOWN_BASEBALL_TERM",
    "MISSING_LOCAL_DATA",
    "STALE_LOCAL_DATA",
    "UNSUPPORTED_LOCAL_INFORMATION",
    "UNSUPPORTED_LOCAL_ANALYTICS",
    "MISSING_SQL_SEMANTICS",
    "UNKNOWN_LOCAL_METRIC",
    "INSUFFICIENT_LOCAL_COVERAGE",
]

RECOVERY_CODES: tuple[str, ...] = (
    "UNKNOWN_ENTITY",
    "UNKNOWN_NICKNAME",
    "UNKNOWN_METRIC",
    "UNKNOWN_BASEBALL_TERM",
    "MISSING_LOCAL_DATA",
    "STALE_LOCAL_DATA",
    "UNSUPPORTED_LOCAL_INFORMATION",
    "UNSUPPORTED_LOCAL_ANALYTICS",
    "MISSING_SQL_SEMANTICS",
    "UNKNOWN_LOCAL_METRIC",
    "INSUFFICIENT_LOCAL_COVERAGE",
)

# A conservative but meaningful deterministic set of "vague analytical" cues. Vague
# concepts like "better" are not forced into a single metric: they become an analysis
# strategy and a set of supporting tasks.
_VAGUE_ANALYTICAL_CUES: tuple[str, ...] = (
    "更好", "誰更好", "谁更好", "更强", "更強", "最擅长", "最擅長", "最危险", "最危險",
    "状态", "狀態", "变强", "變強", "下滑", "最近怎么", "最近為什麼", "为什么", "為什麼",
    "怎么突然", "怎麼突然", "表现如何", "表現如何", "打得", "打的",
    "变化", "變化", "改变", "改變", "对比", "對比", "比较", "比較",
    "better", "best", "hotter", "coldest", "struggling", "most dangerous",
    "recent form", "slumping", "who has been", "how has", "why have", "why did",
    "change", "compare", "comparison", "season performance",
)


def detect_analysis_strategy(raw_query: str) -> str:
    """Return a documented analytical strategy for vague intent, else an empty string.

    The strategy is deliberately not a single metric. It tells the Planner to build a
    balanced profile, which is exactly the property v0.1 lacked.
    """
    lowered = raw_query.casefold()
    if not any(cue.casefold() in lowered for cue in _VAGUE_ANALYTICAL_CUES):
        return ""
    if any(cue in raw_query for cue in ("变化", "變化", "改变", "改變", "对比", "對比",
                                        "比较", "比較", "compare", "comparison")):
        return ("Compare the same metric and population across the requested windows, "
                "holding the filters constant so only the time window changes.")
    if any(cue in raw_query for cue in ("打得", "打的", "更好", "谁更好", "誰更好", "better",
                                        "hotter", "recent form", "who has been")):
        return ("Compare recent offensive performance with a balanced profile: OPS, OBP, "
                "SLG, HR, BB% and K%, supplemented by quality-of-contact metrics where "
                "local coverage allows, rather than selecting one metric.")
    if any(cue in raw_query for cue in ("最擅长", "最擅長", "most dangerous", "best")):
        return ("Rank the population by a bounded profile of the requested skill "
                "(for example performance on the requested pitch type/location) and "
                "explain the chosen indicators and qualification.")
    if any(cue in raw_query for cue in ("状态", "狀態", "下滑", "变强", "變強", "struggling",
                                        "slumping", "recent form")):
        return ("Describe the player's recent form using multiple indicators across the "
                "requested window, and disclose the window's coverage before concluding.")
    return ("Interpret the goal using a bounded multi-indicator analysis and state the "
            "assumptions used.")


class SemanticUnderstanding(Contract):
    """Partially structured, open-world interpretation of one user request.

    ``frozen`` and ``extra='forbid'`` still apply, but only to the *shape* of the
    container. Free-form text fields may hold arbitrary natural language so the semantic
    layer never becomes an information bottleneck before planning.
    """

    raw_query: str = Field(min_length=1)
    user_goal: str = Field(default="", max_length=MAX_FREE_TEXT)
    semantic_brief: str = Field(default="", max_length=MAX_FREE_TEXT)
    planner_notes: str = Field(default="", max_length=MAX_FREE_TEXT)
    analysis_strategy: str = Field(default="", max_length=MAX_FREE_TEXT)

    entities: tuple[Entity, ...] = ()
    candidate_entity_mentions: tuple[str, ...] = ()
    time_hints: tuple[str, ...] = ()
    known_constraints: tuple[Constraint, ...] = ()

    unresolved_concepts: tuple[str, ...] = ()
    ambiguities: tuple[str, ...] = ()
    assumptions_allowed: tuple[str, ...] = ()
    search_hints: tuple[str, ...] = ()
    interpretations: tuple[str, ...] = ()
    recovery_codes: tuple[str, ...] = ()

    @property
    def has_unresolved(self) -> bool:
        return bool(self.unresolved_concepts or self.candidate_entity_mentions
                    or self.recovery_codes)

    def with_recovery(self, *codes: str) -> "SemanticUnderstanding":
        merged = tuple(dict.fromkeys((*self.recovery_codes, *codes)))
        return self.model_copy(update={"recovery_codes": merged})

    def summary(self) -> tuple[str, ...]:
        """Bounded observability lines; never hidden model reasoning."""
        lines: list[str] = []
        if self.user_goal:
            lines.append(f"user_goal: {self.user_goal}")
        if self.analysis_strategy:
            lines.append(f"analysis_strategy: {self.analysis_strategy}")
        if self.candidate_entity_mentions:
            lines.append("candidate_entities: " + ", ".join(self.candidate_entity_mentions))
        if self.unresolved_concepts:
            lines.append("unresolved_concepts: " + ", ".join(self.unresolved_concepts))
        if self.recovery_codes:
            lines.append("recovery_codes: " + ", ".join(self.recovery_codes))
        if self.search_hints:
            lines.append("search_hints: " + " | ".join(self.search_hints[:8]))
        return tuple(lines)
