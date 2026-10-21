"""Web Research v2 language policy (M2-C, ADR 0027).

Separate four distinct language roles:

- ``user_language``            — the language the user wrote in;
- ``primary_research_language`` — the language search *starts* in (English by default);
- ``source_language``          — the language of a retrieved document (detected, never assumed);
- ``response_language``        — the language the final answer is composed in.

The policy is **English-first, not English-only**. English maximises discovery coverage for
MLB official information, journalism, interviews and analysis. User language must never
determine the primary search language (a Chinese question is researched in English first and
answered in Chinese). Context-specific multilingual expansion happens only when triggered, so
the system never turns every query into a multilingual search.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping

DEFAULT_PRIMARY_LANGUAGE = "en"

# Markers that a Goal explicitly asks for a regional / language-community perspective.
_COMMUNITY_MARKERS = (
    "日本媒体", "日本媒體", "日媒", "中文媒体", "中文媒體", "华语", "華語", "中文社区",
    "韓國媒體", "韩国媒体", "韓媒", "當地媒體", "当地媒体", "local media", "national media",
    "japanese media", "chinese media", "korean media", "dominican media", "spanish media",
    "media report", "媒体怎么", "媒體怎麼", "媒体如何", "當地報道", "当地报道",
)

# Markers whose primary source is likely to originate outside English.
_ORIGIN_MARKERS: tuple[tuple[str, str], ...] = (
    ("日本", "ja"), ("日语", "ja"), ("日語", "ja"), ("npb", "ja"), ("japanese", "ja"),
    ("韩国", "ko"), ("韓國", "ko"), ("韩语", "ko"), ("韓語", "ko"), ("kbo", "ko"), ("korean", "ko"),
    ("多米尼加", "es"), ("墨西哥", "es"), ("委内瑞拉", "es"), ("古巴", "es"),
    ("dominican", "es"), ("mexican", "es"), ("venezuelan", "es"), ("cuban", "es"),
    ("puerto rican", "es"),
    ("spanish", "es"), ("español", "es"), ("latino", "es"),
    ("中文", "zh"), ("华语", "zh"), ("华文", "zh"), ("chinese", "zh"), ("台灣", "zh"), ("台湾", "zh"),
)

# Research targets that are inherently language/community scoped.
_COMMUNITY_SCOPE_MARKERS = (
    "slang", "meme", "nickname", "fan discourse", "fan opinion", "community opinion",
    "local terminology", "colloquial", "俚語", "俚语", "绰号", "綽號", "昵称", "昵稱",
    "球迷", "粉丝", "社群", "論壇", "论坛", "圈子", "网络用语", "網絡用語",
)

_LANGUAGE_NAMES = {"ja": "Japanese", "es": "Spanish", "ko": "Korean", "zh": "Chinese"}


@dataclass(frozen=True)
class LanguageAwareAliases:
    """Language-aware aliases for one entity.

    Aliases may shape search queries; they are **never** Evidence. Identity still requires the
    existing entity-resolution rules.
    """

    canonical_name: str
    aliases_by_language: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def aliases(self, language: str | None = None) -> tuple[str, ...]:
        if language is None:
            return tuple(alias for values in self.aliases_by_language.values() for alias in values)
        return tuple(self.aliases_by_language.get(language, ()))

    def all_names(self) -> tuple[str, ...]:
        names = [self.canonical_name]
        names.extend(self.aliases())
        seen: set[str] = set()
        unique: list[str] = []
        for name in names:
            if name and name not in seen:
                seen.add(name)
                unique.append(name)
        return tuple(unique)


@dataclass(frozen=True)
class ResearchLanguagePolicy:
    user_language: str
    source_language: str = ""
    response_language: str = ""
    primary_research_language: str = DEFAULT_PRIMARY_LANGUAGE
    research_languages: tuple[str, ...] = (DEFAULT_PRIMARY_LANGUAGE,)
    expansion_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "user_language": self.user_language,
            "primary_research_language": self.primary_research_language,
            "research_languages": list(self.research_languages),
            "source_language": self.source_language,
            "response_language": self.response_language,
            "expansion_reasons": list(self.expansion_reasons),
        }


def detect_language(text: str) -> str:
    """Cheap script-based language guess (not a full classifier)."""
    text = text or ""
    if re.search(r"[\u3040-\u30ff]", text):
        return "ja"
    if re.search(r"[\uac00-\ud7af]", text):
        return "ko"
    if re.search(r"[\u4e00-\u9fff]", text):
        return "zh"
    if re.search(r"[áéíóúñ¿¡]", text.lower()):
        return "es"
    return "en"


def expansion_languages(
    goal_text: str,
    *,
    mandatory_unsupported: bool = False,
    known_locales: Iterable[str] = (),
    source_hint: str = "",
) -> tuple[list[str], list[str]]:
    """Return ``(languages, reasons)`` for context-specific multilingual expansion (C3).

    Expansion is deliberately narrow: it fires only on one of the four documented triggers and
    never merely because the user wrote in a non-English language.
    """
    text = (goal_text or "").lower()
    languages: list[str] = []
    reasons: list[str] = []

    community = any(marker.lower() in text for marker in _COMMUNITY_MARKERS)
    scoped = any(marker.lower() in text for marker in _COMMUNITY_SCOPE_MARKERS)
    origin: list[str] = [language for marker, language in _ORIGIN_MARKERS if marker.lower() in text]

    if community:
        reasons.append("goal_requests_regional_perspective")
    if scoped:
        reasons.append("language_or_community_scoped_target")
    for language in origin:
        if language not in languages:
            languages.append(language)
        reasons.append(f"likely_primary_source_language:{language}")
    for language in known_locales:
        if language and language not in languages:
            languages.append(language)
            reasons.append(f"known_locale:{language}")
    if source_hint:
        detected = detect_language(source_hint)
        if detected != "en" and detected not in languages:
            languages.append(detected)
            reasons.append(f"source_hint_language:{detected}")
    if mandatory_unsupported and not languages:
        reasons.append("english_left_mandatory_unsupported_no_known_locale")
    # Always research English first; expansions are additive and de-duplicated.
    research = [DEFAULT_PRIMARY_LANGUAGE] + [language for language in languages if language != "en"]
    return research, reasons


def build_policy(
    *,
    user_language: str,
    goal_text: str = "",
    primary_research_language: str = DEFAULT_PRIMARY_LANGUAGE,
    mandatory_unsupported: bool = False,
    known_locales: Iterable[str] = (),
    source_hint: str = "",
    source_language: str = "",
) -> ResearchLanguagePolicy:
    research, reasons = expansion_languages(
        goal_text,
        mandatory_unsupported=mandatory_unsupported,
        known_locales=known_locales,
        source_hint=source_hint,
    )
    if primary_research_language != DEFAULT_PRIMARY_LANGUAGE:
        research = [primary_research_language] + [lang for lang in research if lang != primary_research_language]
    return ResearchLanguagePolicy(
        user_language=user_language or "en",
        source_language=source_language,
        response_language=user_language or "en",
        primary_research_language=primary_research_language,
        research_languages=tuple(dict.fromkeys(research)),
        expansion_reasons=tuple(dict.fromkeys(reasons)),
    )


def language_name(language: str) -> str:
    return _LANGUAGE_NAMES.get(language, language)
