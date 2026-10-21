"""Open retrieval preferences for the information environment named by a request.

These labels choose query order and candidate preference. They are neither factual
authority nor exclusive routing rules; every resulting body still needs verification.
"""

from __future__ import annotations

from enum import Enum


class SourceIntent(str, Enum):
    PRIMARY_STATEMENT = "PRIMARY_STATEMENT"
    INTERVIEW = "INTERVIEW"
    LOCAL_REPORTING = "LOCAL_REPORTING"
    COMMUNITY_DISCUSSION = "COMMUNITY_DISCUSSION"
    OFFICIAL_CURRENT = "OFFICIAL_CURRENT"
    HISTORICAL_REFERENCE = "HISTORICAL_REFERENCE"
    ANALYSIS = "ANALYSIS"
    STRUCTURED_RECORD = "STRUCTURED_RECORD"


_MARKERS: tuple[tuple[SourceIntent, tuple[str, ...]], ...] = (
    (SourceIntent.COMMUNITY_DISCUSSION, ("community", "forum", "fan reaction", "fan opinion", "discussion", "社区", "社區", "论坛", "論壇", "球迷", "팬", "コミュニティ")),
    (SourceIntent.INTERVIEW, ("interview", "transcript", "press conference", "podcast", "采访", "採訪", "专访", "專訪", "インタビュー")),
    (SourceIntent.PRIMARY_STATEMENT, ("official statement", "said in a statement", "statement from", "声明", "聲明", "公式発表")),
    (SourceIntent.LOCAL_REPORTING, ("local media", "local reporting", "japanese media", "chinese media", "korean media", "spanish media", "日本媒体", "日本媒體", "中文媒体", "中文媒體", "当地媒体", "當地媒體", "スポーツメディア", "メディア", "報じ", "報道", "prensa", "언론", "보도")),
    (SourceIntent.OFFICIAL_CURRENT, ("official", "current roster", "injury report", "team update", "league update")),
    (SourceIntent.STRUCTURED_RECORD, ("statistic", "leaderboard", "box score", "official record")),
    (SourceIntent.HISTORICAL_REFERENCE, ("history", "historical", "origins", "历史", "歷史")),
)


def infer_source_intent(text: str) -> SourceIntent:
    lowered = (text or "").lower()
    for intent, markers in _MARKERS:
        if any(marker in lowered for marker in markers):
            return intent
    return SourceIntent.ANALYSIS


def source_type_for_hit(engine: str, intent: SourceIntent) -> str:
    engine = (engine or "").lower()
    if "wikipedia" in engine:
        return "ENCYCLOPEDIA"
    if engine.startswith("feed:") or engine == "publisher_feeds":
        return "FEED_DISCOVERED_JOURNALISM"
    if engine in {"google_news", "broad_search", "endpoint", "tavily"}:
        return "DISCOVERED_WEB"
    return intent.value
