"""Central Shared Knowledge for stable baseball / analytical terminology.

Purpose
-------
Before this module the same finite vocabulary (role words, handedness synonyms,
inning ordinals, count number words, competition names, task/presentation connectives)
was re-declared inside several semantic modules. Scattering stable terminology across
business logic makes it impossible to audit what the system actually knows and invites
copy-paste drift.

This module is deliberately a *closed-world terminology layer*, not a natural-language
parser. It owns only:

* stable baseball abbreviations and synonyms (handedness, roles, competition names);
* finite number words and inning ordinals;
* the closed task/presentation connectives used for residue accounting.

It must **not** grow into an enumeration of user sentences. Open-ended meaning belongs to
the semantic interpreter; schema grounding validates the resulting candidate. See
``docs/audit/NATURAL_LANGUAGE_HARDCODING_AUDIT.md`` for the classification rules
(A legitimate domain rule, B stable terminology, C open-ended language, D regression patch).
"""

from __future__ import annotations

# --------------------------------------------------------------------------- B: roles
# Stable role nouns. A role word is not an individual identity and not a relation.
ROLE_WORDS: frozenset[str] = frozenset(
    {
        "player", "players", "pitcher", "pitchers", "batter", "batters", "hitter",
        "hitting", "hitters", "pitching", "batting",
        "投手", "打者", "球员", "球員",
    }
)

# --------------------------------------------------------------------- B: handedness
# Canonical semantic side for stable handedness terminology. The value is a governed
# enum value ('L'/'R'); the key is the canonical user-facing concept.
HANDEDNESS_PHRASES: tuple[tuple[str, str], ...] = (
    (r"\bsouthpaws?\b|\bleft[ -]?handers?\b|\blefties?\b|左投手|左投", "left-handed pitcher"),
    (r"\bright[ -]?handers?\b|\brighties?\b|右投手|右投", "right-handed pitcher"),
)

# Stable opposition connectives. Only used to validate a one-edit grammatical typo
# beside an independently parsed opposing-hand obligation; never to infer a filter.
OPPOSITION_CONNECTIVES: frozenset[str] = frozenset({"against", "versus", "vs"})

# Short side tokens used by the finite handedness/count syntax.
SIDE_VALUES: dict[str, str] = {
    "left": "L", "l": "L", "左": "L",
    "right": "R", "r": "R", "右": "R",
}

# Stable pitch-classification terminology (Statcast enum codes). Classification B:
# known baseball vocabulary, not sentence enumeration.
PITCH_TYPE_NAMES: dict[str, str] = {
    "fastball": "FF", "four-seam": "FF", "four seam": "FF", "fourseam": "FF", "4-seam": "FF", "4 seam": "FF",
    "sinker": "SI", "two-seam": "SI", "two seam": "SI",
    "slider": "SL", "sweeper": "ST",
    "cutter": "FC", "cut fastball": "FC",
    "changeup": "CH", "change-up": "CH", "change up": "CH", "change": "CH",
    "curveball": "CU", "curve": "CU", "knuckle curve": "KC", "knuckle-curve": "KC",
    "splitter": "FS", "split-finger": "FS", "split finger": "FS",
    "knuckleball": "KN", "knuckler": "KN",
    "screwball": "SC", "forkball": "FO", "eephus": "EP",
    "滑球": "SL", "快速球": "FF", "曲球": "CU", "变速球": "CH", "變速球": "CH",
    "伸卡球": "SI", "卡特球": "FC", "指叉球": "FS", "蝴蝶球": "KN",
}

# --------------------------------------------------------------------- B: populations
POPULATION_TERMS: dict[str, str] = {
    "regular season": "REGULAR_SEASON",
    "regular-season": "REGULAR_SEASON",
    "常规赛": "REGULAR_SEASON",
    "常規賽": "REGULAR_SEASON",
    "例行赛": "REGULAR_SEASON",
    "例行賽": "REGULAR_SEASON",
    "postseason": "POSTSEASON",
    "playoffs": "POSTSEASON",
    "季后赛": "POSTSEASON",
    "季後賽": "POSTSEASON",
    "world series": "WORLD_SERIES",
    "世界大赛": "WORLD_SERIES",
    "世界大賽": "WORLD_SERIES",
    "spring training": "SPRING_TRAINING",
    "春训": "SPRING_TRAINING",
    "春訓": "SPRING_TRAINING",
}

# ------------------------------------------------------------------- B: inning ordinals
INNING_WORDS: dict[str, int] = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8,
    "九": 9, "十": 10,
}

# ---------------------------------------------------------------------- B: count words
COUNT_WORDS: dict[str, int] = {
    "zero": 0, "one": 1, "two": 2, "three": 3,
    "零": 0, "一": 1, "两": 2, "兩": 2, "二": 2, "三": 3,
}

# ------------------------------------------------------- C: task/presentation connectives
# Closed vocabulary of task/presentation words. It is NOT a relevance test by keyword
# absence: only tokens that survive removal of consumed spans, recognised role cues and
# this closed vocabulary are treated as semantically relevant. Anything unknown remains
# unresolved. This list is intentionally finite and must not be treated as the primary
# mechanism for understanding open-ended language (M2.12R4 audit §15).
NON_SEMANTIC_WORDS: frozenset[str] = frozenset(
    {
        "the", "a", "an", "of", "for", "with", "among", "only", "results", "result",
        "by", "and", "or", "to", "in", "on", "at", "from", "during", "please", "me",
        "his", "her", "their", "its", "that", "those", "these", "this", "is", "are",
        "was", "were", "be", "report", "show", "list", "find", "get", "give",
        "provide", "calculate", "compute", "determine", "identify", "analyze", "analyse",
        "analysis", "metric", "metrics", "stat", "stats", "statistic", "statistics",
        "performance", "data", "numbers", "number", "summary", "overview", "breakdown",
        "line", "value", "values", "all", "each", "per", "as", "vs", "versus",
        "there", "here", "total", "overall", "bound", "requested", "specified",
        "given", "relevant", "desired", "target", "following", "above", "below",
        "into", "from", "out", "up", "down", "back", "same", "other", "both",
        # Comparison / direction / change wording is answer-changing semantics, not task
        # presentation. It must stay relevant residue so a clause that asks for a change,
        # trend or difference is only UNDERSTOOD when a governed comparison obligation
        # actually represents it (audit GA-03).
        "aggregate", "aggregated", "aggregation", "produce", "produced", "summarize",
        "summarise", "collect", "obtain", "derive", "derived", "measured", "measure",
        "measures", "baseball", "while", "when", "against", "count",
        "hit", "thrown", "threw", "draw", "drew", "deliver", "delivered", "mph",
        "recorded", "between", "minus", "offensive", "totals", "how", "did", "do", "does", "doing",
    }
)

# Multilingual domain/question phrases already accounted for by an authoritative
# structured obligation (population, role, season, question form). They are removed from
# residue before the conservative relevance test. This is finite Shared Knowledge about
# stable baseball concepts, not a general NL parser.
DOMAIN_ACCOUNTED_PHRASES: tuple[str, ...] = (
    r"季後賽|季后赛|常規賽|常规赛|世界大賽|世界大赛|外卡賽|外卡赛|外卡|分區賽|分区赛|"
    r"聯盟冠軍賽|联盟冠军赛|春訓|春训|例行賽|例行赛",
    r"打擊|打击|投球|打者|投手|球員|球员|擊球|击球|跑壘|跑垒|守備|守备",
    r"表現如何|表现如何|表現|表现|如何|怎麼樣|怎么样|是多少|多少|數據|数据|統計|统计|"
    r"指標|指标|情況|情况|狀態|状态|排名|排行|分別|分别",
    r"和|與|与|及|以及|並且|并且|各|的|了|是|有|在|為|为|之|其|個|个|次|支|名",
    r"賽季|赛季|球季|年",
    r'的是谁|的是誰|誰|谁',
    r"请|請|一共|總共|总共|打出|投出|统计|統計|数|數|提供|給出|给出|相差|那|呢",
)
