"""M2-C: research-language policy (English-first, not English-only)."""

from __future__ import annotations

from app.web.language import (
    LanguageAwareAliases,
    build_policy,
    expansion_languages,
)


def test_chinese_user_query_researches_in_english_and_answers_in_chinese():
    policy = build_policy(user_language="zh", goal_text="大谷翔平最近改变了什么打击策略？")
    assert policy.user_language == "zh"
    assert policy.primary_research_language == "en"
    assert policy.research_languages[0] == "en"
    assert policy.response_language == "zh"
    # A plain non-English user query does not by itself trigger multilingual expansion.
    assert policy.research_languages == ("en",)


def test_japanese_origin_source_triggers_japanese_expansion():
    languages, reasons = expansion_languages("日本媒体如何评价大谷翔平的手术？")
    assert "ja" in languages
    assert any("ja" in reason for reason in reasons)


def test_chinese_community_scoped_query_triggers_chinese_expansion():
    languages, reasons = expansion_languages("中文棒球社区如何评价这笔交易？")
    assert "zh" in languages
    assert any("source_language" in reason or "locale" in reason or "community" in reason for reason in reasons)


def test_dominican_media_question_triggers_spanish_expansion():
    languages, _reasons = expansion_languages("What did Dominican media report about the signing?")
    assert "es" in languages


def test_plain_english_question_does_not_expand():
    languages, reasons = expansion_languages("Explain the causes of the 1994 strike.")
    assert languages == ["en"]
    assert reasons == []


def test_known_locale_triggers_expansion_without_goal_marker():
    languages, reasons = expansion_languages("Injury update", known_locales=["ja"])
    assert "ja" in languages
    assert any("known_locale" in reason for reason in reasons)


def test_language_aware_aliases_shape_queries_but_are_not_evidence():
    aliases = LanguageAwareAliases(
        canonical_name="Shohei Ohtani",
        aliases_by_language={"en": ("Shohei Ohtani",), "ja": ("大谷翔平", "大谷選手"), "zh": ("大谷翔平",)},
    )
    assert aliases.aliases("ja") == ("大谷翔平", "大谷選手")
    assert set(aliases.all_names()) == {"Shohei Ohtani", "大谷翔平", "大谷選手"}


def test_policy_serializes_all_four_language_roles():
    policy = build_policy(user_language="zh", goal_text="日本媒体怎么看？", source_language="ja")
    data = policy.to_dict()
    assert set(data) >= {"user_language", "primary_research_language", "source_language", "response_language"}
    assert data["primary_research_language"] == "en"
