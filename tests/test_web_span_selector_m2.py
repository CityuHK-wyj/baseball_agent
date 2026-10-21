"""M2-1 / M2-4: proposition-aware span selection and candidate admission."""

from __future__ import annotations

from app.web.span_selector import select_span_dicts, select_spans, support_eligible


def _proposition(**overrides):
    value = {
        "statement": "The 1994 Major League Baseball strike was caused by a salary cap dispute.",
        "kind": "EXPLANATION",
        "subject": "1994 Major League Baseball strike",
        "time_scope": {"season": 1994},
        "population": None,
        "required_information": "strike causes salary cap",
        "operation": "",
        "mandatory": True,
    }
    value.update(overrides)
    return value


LONG_BODY = (
    "Major League Baseball is a professional baseball organization in North America. "
    "Major League Baseball was founded in 1876 and has thirty teams. "
    "The commissioner of Major League Baseball oversees the league. "
    "Major League Baseball playoffs include the World Series each season. "
    "References\n"
    "Link to salary cap article\n"
    "Link to 1994 season\n"
    "Retrieved from the original archive\n"
    "External links\n"
    "Link to collective bargaining\n"
    "Link to players union\n"
    "Major League Baseball continued to expand during the 1990s. "
    "The 1994 Major League Baseball strike was caused by a dispute over a proposed salary cap. "
    "The work stoppage led to the cancellation of the 1994 World Series. "
    "Owners and the players union disagreed about revenue sharing for many months. "
)


def test_selects_relevant_passage_in_long_document_with_exact_offsets():
    selections = select_spans(LONG_BODY, query="1994 strike causes salary cap", propositions=[_proposition()])
    assert selections
    top = selections[0]
    assert "salary cap" in top.text
    assert LONG_BODY[top.start : top.end] == top.text
    assert top.quality == "prose"
    assert top.required_matched


def test_front_matter_and_citation_lists_are_not_preferred():
    selections = select_spans(LONG_BODY, query="1994 strike causes salary cap", propositions=[_proposition()])
    # The top selection must be the causal sentence, not the lead or a "Link to" fragment.
    assert "Link to" not in selections[0].text
    assert selections[0].text.strip() != LONG_BODY.split(".")[0].strip()
    # No selected passage may be a navigation/citation list.
    for selection in selections:
        assert selection.quality != "navigation"


def test_coreference_window_merges_adjacent_subject_and_relation():
    body = (
        "The work stoppage began on August 12, 1994. "
        "It was caused by a dispute over the proposed salary cap. "
        "The season ended without a World Series. "
    )
    proposition = _proposition(subject="1994 Major League Baseball strike", required_information="caused salary cap")
    selections = select_spans(body, query="strike cause", propositions=[proposition])
    assert selections
    # The relation sentence lacks the full subject; the window should recover context from a
    # neighbouring sentence without breaking the offset invariant.
    for selection in selections:
        assert body[selection.start : selection.end] == selection.text
    assert any(selection.windowed for selection in selections) or any(
        "salary cap" in selection.text for selection in selections
    )


def test_support_eligible_blocks_subject_only_citation_fragments():
    citation = {"text": "Link to Judge quotes Link to Judge video", "quality": "citation"}
    assert support_eligible(citation, {"injury"}) is False
    assert support_eligible(citation, {"quotes"}) is True
    prose = {"text": "The player was placed on the injured list.", "quality": "prose"}
    assert support_eligible(prose, {"injury"}) is True


def test_select_span_dicts_are_offset_valid_and_carry_quality():
    dicts = select_span_dicts(LONG_BODY, query="1994 strike", propositions=[_proposition()])
    assert dicts
    for span in dicts:
        assert LONG_BODY[span["start"] : span["end"]] == span["text"]
        assert span["source_type"] == "DOCUMENT_BODY"
        assert span["retrieval_status"] == "OK"
        assert span["quality"] in {"prose", "citation", "navigation", "metadata", "fallback"}


def test_chinese_document_offsets_and_selection():
    body = (
        "美国职业棒球大联盟是北美最高级别的职业棒球联赛。"
        "该联盟成立于1876年，目前共有三十支球队。"
        "1994年美国职棒大联盟罢工的起因是劳资双方就工资帽问题产生分歧。"
        "这次罢赛导致1994年世界大赛被取消。"
    )
    proposition = _proposition(
        subject="1994年美国职棒大联盟罢工",
        required_information="罢工 起因 工资帽",
        statement="1994年美国职棒大联盟罢工的起因是工资帽争议。",
    )
    selections = select_spans(body, query="罢工 起因 工资帽", propositions=[proposition])
    assert selections
    assert "工资帽" in selections[0].text
    assert body[selections[0].start : selections[0].end] == selections[0].text


def test_fallback_is_offset_valid():
    body = "A short unrelated document about gardening and flowers and trees."
    selections = select_spans(body, query="strike", propositions=[_proposition()])
    assert selections
    for selection in selections:
        assert body[selection.start : selection.end] == selection.text
