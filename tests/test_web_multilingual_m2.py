"""M2-5: multilingual expansion is context-triggered, English-first, and never translates evidence."""

from __future__ import annotations

from app.capabilities.web_research import WebResearchTool
from app.capabilities.web_search import FetchedPage
from app.config import WebConfig
from app.contracts.models import AnswerProposition, PropositionKind, ToolRequest


class _Hit:
    def __init__(self, url, title, engine="fixture"):
        self.url = url
        self.title = title
        self.snippet = ""
        self.engine = engine


class _Backend:
    name = "fixture"

    def __init__(self, hits):
        self._hits = list(hits)
        self.last_error = ""

    def search(self, query, *, limit=5, language="en"):
        return list(self._hits)


def _proposition(subject, required, statement):
    return AnswerProposition(
        statement=statement,
        kind=PropositionKind.FACT,
        subject=subject,
        required_information=required,
        mandatory=True,
    )


def _run(monkeypatch, query, language, proposition, body):
    tool = WebResearchTool(WebConfig(broad_search=False), llm=None)
    tool.backends = [_Backend([_Hit("https://example.com/a", "source")])]

    def fake_fetch(url, config):
        return FetchedPage(
            url=url, final_url=url, title="source", text=body,
            retrieved_at="2026-09-01T00:00:00Z", ok=True, content_hash="hash",
        )

    monkeypatch.setattr("app.capabilities.web_research.fetch_page", fake_fetch)
    request = ToolRequest(
        id="tr",
        need_id="n_web",
        capability="web_research",
        objective=query,
        inputs={
            "entity_names": [proposition.subject],
            "objective": query,
            "user_wording": query,
            "language": language,
            "propositions": [proposition.model_dump(exclude={"coverage"})],
        },
    )
    return tool.execute(request)


def test_plain_chinese_query_researches_in_english_only(monkeypatch):
    proposition = _proposition(
        "大谷翔平", "打击策略调整",
        "大谷翔平最近改变了他的打击策略。",
    )
    body = "大谷翔平最近改变了他的打击策略，以提高击球率。" * 4
    result = _run(monkeypatch, "大谷翔平最近改变了什么打击策略？", "zh", proposition, body)
    policy = result.artifact.structured_data["language_policy"]
    assert policy["user_language"] == "zh"
    assert policy["primary_research_language"] == "en"
    assert policy["research_languages"] == ["en"]
    assert policy["response_language"] == "zh"


def test_japanese_origin_question_triggers_japanese_expansion(monkeypatch):
    proposition = _proposition(
        "Shohei Ohtani", "injury update",
        "Shohei Ohtani injury update.",
    )
    body = "Shohei Ohtani injury update: the team announced a minor strain." * 4
    result = _run(monkeypatch, "日本媒体怎么看大谷翔平的伤病更新？", "zh", proposition, body)
    policy = result.artifact.structured_data["language_policy"]
    assert policy["research_languages"][0] == "ja"
    assert policy["primary_research_language"] == "ja"
    assert "ja" in policy["research_languages"]
    plan = result.artifact.structured_data["query_plan"]
    assert any(variant["language"] == "ja" for variant in plan["variants"])


def test_chinese_community_question_triggers_chinese_expansion(monkeypatch):
    proposition = _proposition("Shohei Ohtani", "trade reaction", "trade reaction")
    body = "Shohei Ohtani trade reaction from around the league." * 4
    result = _run(monkeypatch, "中文棒球社区如何评价这笔交易？", "zh", proposition, body)
    policy = result.artifact.structured_data["language_policy"]
    assert "zh" in policy["research_languages"]


def test_original_language_evidence_is_never_translated(monkeypatch):
    proposition = _proposition(
        "大谷翔平", "打击策略调整",
        "大谷翔平最近改变了他的打击策略。",
    )
    body = "大谷翔平最近改变了他的打击策略，以提高击球率。" * 4
    result = _run(monkeypatch, "大谷翔平最近改变了什么打击策略？", "zh", proposition, body)
    data = result.artifact.structured_data
    document = data["documents"][0]
    assert document["body_text"] == body
    assert data["evidence_spans"]
    for span in data["evidence_spans"]:
        # Original-language evidence and the exact offset invariant are preserved; no
        # translation replaces the source span.
        assert document["body_text"][span["start"] : span["end"]] == span["text"]
        assert any("\u4e00" <= ch <= "\u9fff" for ch in span["text"])
