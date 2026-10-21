"""Canonical relation-semantics vocabulary and builder (M2 architecture correction).

Before this module the same relationship vocabulary was re-declared independently in
``app/web/query_plan.py``, ``app/web/ranking.py`` and ``app/web/span_selector.py`` (and the
high-risk/current markers in ``app/semantic/goals.py``). One semantic meaning was therefore
guessed several times. This module is the single definition of that meaning and builds the
canonical :class:`~app.contracts.models.RelationSemantics` carried by a ``UserRequirement``.

It is capability-agnostic. Consumers:

- Planner capability selection may *read* ``relation_kind`` as a routing signal;
- ``web_research`` query planning/ranking/span selection use the relation cues;
- verifier preparation uses :func:`relation_cue_tokens` to make the required-information
  match relation-aware (it never weakens the verifier hard gates);
- local analytics / dynamic fact may read the same signal, each keeping its own proof
  mechanism.

``RelationSemantics`` is interpretation, never evidence: it can direct cognition but can
never itself satisfy a factual obligation or bypass Artifact/Judge governance.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from app.contracts.models import RelationKind, RelationSemantics

# --------------------------------------------------------------------------- vocabularies
# Discovery cues shape search facets. Support cues are prose markers that establish the
# relationship. They are deliberately kept together so query planning and span selection
# cannot drift apart again.

CAUSE_QUERY_CUES: tuple[str, ...] = (
    "cause", "causes", "caused", "reason", "reasons", "why", "explain",
    "原因", "为何", "為何", "为什么", "為什麼", "起因",
)
CAUSE_SUPPORT_CUES: tuple[str, ...] = (
    "caused", "because", "due to", "reason", "in response to", "resulting from",
    "stemmed from", "stemming from", "triggered by", "dispute", "dispute over",
    "opposed", "refused", "proposed", "proposal", "expiration", "expired",
    "起因", "原因", "由于", "由於", "因为", "因為", "导火索", "導火索",
)

EFFECT_QUERY_CUES: tuple[str, ...] = (
    "effect", "effects", "impact", "consequence", "consequences", "result", "results",
    "影响", "影響", "后果", "後果", "结果", "結果",
)
EFFECT_SUPPORT_CUES: tuple[str, ...] = (
    "resulted in", "resulting in", "led to", "as a result", "consequence", "consequences",
    "canceled", "cancelled", "cancelation", "cancellation", "impact", "aftermath",
    "the effect", "the effects", "following the", "in the wake",
    "导致", "導致", "影响", "影響", "后果", "後果", "取消", "结果", "結果",
)

QUOTE_QUERY_CUES: tuple[str, ...] = (
    "interview", "interviewed", "quote", "quotes", "said", "telling", "one-on-one",
    "scouting report", "conversation", "podcast", "采访", "專訪", "专访", "表示",
    "谈到", "談到",
)
QUOTE_SUPPORT_CUES: tuple[str, ...] = (
    "said", "told", "quoted", "according to", "interview", "explained", "stated",
    "表示", "說", "说", "谈到", "談到", "采访", "專訪", "专访",
)

# Operational/status facts (mutable state of an entity). ``dynamic_fact`` is the natural
# owner; web research may retrieve a current authoritative source.
STATUS_QUERY_CUES: tuple[str, ...] = (
    "injury", "injured", "roster", "trade", "traded", "status", "lineup", "signed",
    "contract", "surgery", "rehab", "disabled list", "伤病", "受傷", "受伤", "交易",
    "阵容", "陣容", "签约", "簽約", "手术", "手術", "康复", "康復",
)
STATUS_SUPPORT_CUES: tuple[str, ...] = (
    "injury", "injured", "roster", "trade", "traded", "status", "lineup", "signed",
    "contract", "surgery", "rehab", "reinstated", "activated",
    "伤病", "受傷", "受伤", "交易", "阵容", "陣容", "签约", "簽約", "手术", "手術",
)

FRESHNESS_QUERY_CUES: tuple[str, ...] = (
    "recent", "latest", "today", "this week", "currently", "right now", "as of",
    "最近", "最新", "今天", "现在", "目前", "当前", "如今",
)

VALUE_QUERY_CUES: tuple[str, ...] = (
    "how many", "how much", "what is his", "what are his", "what was his",
    "多少", "几次", "几个", "几支", "是多少",
)
VALUE_SUPPORT_CUES: tuple[str, ...] = (
    "home runs", "hr", "avg", "obp", "slg", "ops", "era", "rbi", "whip",
    "strikeouts", "batting average", "earned run average", "本垒打", "打击率", "自责分率",
)

COMPARISON_QUERY_CUES: tuple[str, ...] = (
    "compare", "compared", "comparison", "versus", "vs", "difference", "change",
    "changed", "increase", "increased", "decrease", "decreased", "better", "worse",
    "比较", "比較", "对比", "對比", "变化", "變化", "增加", "减少", "提升", "下降",
)
COMPARISON_SUPPORT_CUES: tuple[str, ...] = (
    "compared", "compared to", "versus", "difference", "changed", "increased",
    "decreased", "higher", "lower", "more than", "fewer than", "less than",
    "比较", "相比", "差异", "變化", "变化", "增加", "减少", "提升", "下降",
)
COMPARISON_UP_CUES: tuple[str, ...] = ("increase", "increased", "higher", "more than", "提升", "增加", "上升")
COMPARISON_DOWN_CUES: tuple[str, ...] = ("decrease", "decreased", "lower", "fewer than", "下降", "减少")

AGGREGATION_QUERY_CUES: tuple[str, ...] = (
    "total", "aggregate", "average", "mean", "sum", "count", "leader", "leaders",
    "most", "fewest", "rank", "ranking", "top",
    "平均", "总计", "總計", "最多", "最少", "排名", "领先", "領先",
)
AGGREGATION_SUPPORT_CUES: tuple[str, ...] = (
    "total", "average", "mean", "sum", "counted", "leader", "most", "fewest",
    "ranked", "led the league", "平均", "总计", "總計", "最多", "最少", "领先", "領先",
)

EXPLANATION_QUERY_CUES: tuple[str, ...] = (
    "why", "explain", "explanation", "reason", "background", "history", "origin", "how come",
    "为什么", "為什麼", "解释", "解釋", "说明", "說明", "背景", "历史", "歷史", "原因",
)
EXPLANATION_SUPPORT_CUES: tuple[str, ...] = (
    "because", "therefore", "as a result", "in order to", "explained", "due to",
    "背景", "因此", "由于", "由於", "因为", "因為",
)

# Section headers (span selection context), kept here so the same relation meaning owns them.
SECTION_CAUSE_CUES: tuple[str, ...] = (
    "background", "causes", "cause", "reasons", "context", "origins", "history",
    "背景", "起因", "原因", "历史", "歷史",
)
SECTION_EFFECT_CUES: tuple[str, ...] = (
    "aftermath", "effects", "effect", "impact", "results", "consequences", "legacy",
    "影响", "影響", "后果", "後果", "后续", "後續",
)

# High-risk topics: an unverified background explanation must not stand in for evidence.
HIGH_RISK_CUES: tuple[str, ...] = (
    "currently", "right now", "as of", "injury", "injured", "traded", "trade",
    "signing", "doping", "steroid", "scandal", "accused", "allegation",
    "controversy", "cheating",
    "现在", "目前", "当前", "如今", "伤病", "受伤", "伤情", "受傷", "交易", "签约",
    "簽約", "被交易", "禁药", "興奮劑", "兴奋剂", "丑闻", "醜聞", "指控", "争议",
    "爭議", "作弊",
)

# The single source of truth for cue lookup by relation kind.
RELATION_QUERY_CUES: dict[RelationKind, tuple[str, ...]] = {
    RelationKind.CAUSE: CAUSE_QUERY_CUES,
    RelationKind.EFFECT: EFFECT_QUERY_CUES,
    RelationKind.QUOTE: QUOTE_QUERY_CUES,
    RelationKind.STATUS: STATUS_QUERY_CUES,
    RelationKind.FRESHNESS: FRESHNESS_QUERY_CUES,
    RelationKind.VALUE: VALUE_QUERY_CUES,
    RelationKind.COMPARISON: COMPARISON_QUERY_CUES,
    RelationKind.AGGREGATION: AGGREGATION_QUERY_CUES,
    RelationKind.EXPLANATION: EXPLANATION_QUERY_CUES,
}
RELATION_SUPPORT_CUES: dict[RelationKind, tuple[str, ...]] = {
    RelationKind.CAUSE: CAUSE_SUPPORT_CUES,
    RelationKind.EFFECT: EFFECT_SUPPORT_CUES,
    RelationKind.QUOTE: QUOTE_SUPPORT_CUES,
    RelationKind.STATUS: STATUS_SUPPORT_CUES,
    RelationKind.VALUE: VALUE_SUPPORT_CUES,
    RelationKind.COMPARISON: COMPARISON_SUPPORT_CUES,
    RelationKind.AGGREGATION: AGGREGATION_SUPPORT_CUES,
    RelationKind.EXPLANATION: EXPLANATION_SUPPORT_CUES,
}

# Kinds whose cue vocabulary may safely extend the required-information match during
# verifier preparation. Status/freshness are routing/recency signals about *when* a fact is
# true, not textual relations that establish it, so they are deliberately excluded: adding
# their (broad) vocabulary would let a merely current page look like support. Identity/scope
# are excluded for the same reason. This keeps deterministic precision high.
RELATIONAL_KINDS: frozenset[RelationKind] = frozenset({
    RelationKind.CAUSE,
    RelationKind.EFFECT,
    RelationKind.QUOTE,
    RelationKind.COMPARISON,
    RelationKind.EXPLANATION,
})

_TOKEN_RE = re.compile(r"[0-9]+(?:\.[0-9]+)?|[a-z][a-z0-9'\-]+|[\u4e00-\u9fff]+")
_CUE_STOPWORDS = {"the", "a", "an", "of", "for", "and", "or", "to", "in", "on", "at", "as"}


def _contains_any(text: str, cues: Iterable[str]) -> bool:
    low = (text or "").lower()
    return any(cue in low for cue in cues)


def _cue_tokens(cues: Iterable[str]) -> set[str]:
    tokens: set[str] = set()
    for cue in cues:
        for match in _TOKEN_RE.finditer(str(cue).lower()):
            token = match.group(0)
            if token in _CUE_STOPWORDS:
                continue
            if all("\u4e00" <= ch <= "\u9fff" for ch in token):
                if len(token) >= 2:
                    tokens.add(token)
            elif len(token) >= 3:
                tokens.add(token)
    return tokens


def relation_kinds_from_text(text: str) -> list[RelationKind]:
    """All relation kinds whose cues occur in ``text``, most-specific first."""
    matched: list[RelationKind] = []
    # Order matters: a cause/effect/quote/status/comparison reading is more informative than a
    # generic explanation reading when both fire.
    for kind in (
        RelationKind.CAUSE,
        RelationKind.EFFECT,
        RelationKind.COMPARISON,
        RelationKind.QUOTE,
        RelationKind.STATUS,
        RelationKind.FRESHNESS,
        RelationKind.AGGREGATION,
        RelationKind.VALUE,
        RelationKind.EXPLANATION,
    ):
        if _contains_any(text, RELATION_QUERY_CUES.get(kind, ())):
            matched.append(kind)
    return matched


def relation_kind_from_text(text: str, default: RelationKind = RelationKind.GENERAL) -> RelationKind:
    kinds = relation_kinds_from_text(text)
    return kinds[0] if kinds else default


def relation_cues(kind: RelationKind, *, include_support: bool = True) -> tuple[str, ...]:
    cues = list(RELATION_QUERY_CUES.get(kind, ()))
    if include_support:
        cues.extend(RELATION_SUPPORT_CUES.get(kind, ()))
    return tuple(dict.fromkeys(cues))


def relation_cue_tokens(semantics: RelationSemantics | None) -> set[str]:
    """Content tokens of the relation's cues, for relation-aware required-information match.

    Returns an empty set for a missing semantics or a non-relational kind, so callers that
    have no canonical interpretation keep their previous lexical behaviour unchanged. A
    multi-label semantics contributes the cues of every relational label it carries.
    """
    if semantics is None:
        return set()
    kinds = [kind for kind in (semantics.relation_kinds or [semantics.relation_kind]) if kind in RELATIONAL_KINDS]
    if not kinds:
        return set()
    cues = list(semantics.semantic_cues) if semantics.semantic_cues else list(relation_cues(kinds[0]))
    tokens = _cue_tokens(cues)
    # The relation kind words themselves are legitimate required cues (e.g. "cause").
    for kind in kinds:
        tokens |= _cue_tokens([kind.value.lower()])
    return tokens


def build_relation_semantics(
    *,
    text: str = "",
    kind: RelationKind | None = None,
    subjects: Iterable[str] = (),
    objects: Iterable[str] = (),
    metrics: Iterable[str] = (),
    time_scope: dict[str, Any] | None = None,
    population: str | None = None,
    original_intent: str = "",
    polarity: str = "AFFIRMED",
) -> RelationSemantics:
    """Build the canonical interpretation for one Requirement.

    ``kind`` is an explicit producer hint (e.g. a MEASURE requirement is always VALUE);
    otherwise the kind is derived from the requirement's own wording. Cues are unioned across
    every kind that fires so a compound wording ("causes and effects") keeps both readings.
    """
    kinds: list[RelationKind] = []
    if kind is not None:
        kinds.append(kind)
    for matched in relation_kinds_from_text(text):
        if matched not in kinds:
            kinds.append(matched)
    if not kinds:
        kinds = [RelationKind.GENERAL]

    cues: list[str] = []
    for item in kinds:
        cues.extend(relation_cues(item))

    direction = ""
    if RelationKind.COMPARISON in kinds:
        low = (text or "").lower()
        if _contains_any(low, COMPARISON_UP_CUES):
            direction = "increase"
        elif _contains_any(low, COMPARISON_DOWN_CUES):
            direction = "decrease"

    return RelationSemantics(
        relation_kind=kinds[0],
        relation_kinds=list(kinds),
        subject_refs=[str(item) for item in subjects if str(item).strip()],
        object_refs=[str(item) for item in objects if str(item).strip()],
        metric_refs=[str(item) for item in metrics if str(item).strip()],
        time_scope=dict(time_scope or {}),
        population=population,
        comparison_direction=direction,
        polarity=polarity,
        semantic_cues=list(dict.fromkeys(cues)),
        source_text=text or original_intent,
    )


def is_high_risk_text(text: str) -> bool:
    return _contains_any(text, HIGH_RISK_CUES)
