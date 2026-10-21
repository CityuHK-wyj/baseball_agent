"""Web Research v2 — M1 tickets (T1…T7) focused regression tests.

These tests are additive to the accepted Phase 5B suite. They exercise the M1 contracts
and the keyless Wikipedia retrieval path without changing the Planner-visible surface.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.capabilities.web_search import FetchedPage, WebSearchConfig
from app.capabilities.web_research import WebResearchTool
from app.config import WebConfig
from app.contracts.models import ToolRequest
from app.observe.diagnostics import DiagnosticRecorder, LatencyTracker
from app.web.budget import (
    GoalResearchBudget,
    ResearchBudget,
    ResearchBudgetUsage,
    effective_budget,
)
from app.web.outcomes import SourceOutcome, classify_search_result
from app.web.sources.base import SearchProvider, SourceDescriptor
from app.web.sources.encyclopedia import WikipediaSource
from app.web.sources.structured import (
    StructuredRecord,
    structured_location,
    structured_source_quality,
)
from app.web.versioning import EXTRACTOR_VERSION, body_hash, body_metadata, can_reuse_offsets, reextract


# --------------------------------------------------------------------------- fixtures


class _FakeBackend:
    def __init__(self, name="fake", hits=None, error="", raises=None):
        self.name = name
        self._hits = hits or []
        self._configured_error = error
        self._raises = raises
        self.last_error = ""

    def search(self, query, *, limit=5, language="en"):
        if self._raises is not None:
            raise self._raises
        self.last_error = self._configured_error
        return list(self._hits)


class _Hit:
    def __init__(self, url="https://example.com/a", title="Shohei Ohtani 2025 home runs", engine="fake"):
        self.url = url
        self.title = title
        self.snippet = ""
        self.engine = engine


def _web_tool(backends):
    tool = WebResearchTool(WebConfig(broad_search=False), llm=None)
    tool.backends = backends
    return tool


def _request(subjects=("Shohei Ohtani",), propositions=None):
    inputs = {
        "entity_names": list(subjects),
        "objective": "2025 home runs",
        "user_wording": "",
    }
    if propositions is not None:
        inputs["propositions"] = propositions
    return ToolRequest(
        need_id="n9",
        capability="web_research",
        objective="2025 home runs",
        inputs=inputs,
    )


def _fake_fetch_ok(url, config, text=None):
    body = text or ("Shohei Ohtani hit fifty five home runs during the 2025 season. " * 5)
    return FetchedPage(
        url=url,
        final_url=url,
        title="Shohei Ohtani 2025",
        text=body,
        retrieved_at="2026-09-27T00:00:00Z",
        ok=True,
        content_hash="deadbeef",
    )


def _ctx(tmp_path):
    recorder = DiagnosticRecorder(tmp_path / "diag.json")
    return SimpleNamespace(diagnostics=recorder, latency=LatencyTracker()), recorder


# ------------------------------------------------------------------------ M1-T1 tests


def test_goal_and_research_budget_contract_defaults():
    goal = GoalResearchBudget()
    assert goal.goal_provider_calls == 24
    assert goal.goal_documents_fetched == 10
    assert goal.goal_llm_query_calls == 2
    assert goal.goal_llm_verify_calls == 12
    assert goal.goal_search_rounds == 3
    assert goal.goal_soft_deadline_ms == 30_000
    assert goal.goal_hard_deadline_ms == 45_000

    tool = ResearchBudget()
    assert tool.max_provider_calls == 8
    assert tool.max_documents_fetched == 4
    assert tool.max_llm_query_calls == 1
    assert tool.max_llm_verify_calls == 6
    assert tool.provider_timeout_ms == 6_000
    assert tool.fetch_timeout_ms == 10_000


def test_effective_budget_is_min_of_tool_and_remaining_goal():
    goal = GoalResearchBudget(
        goal_provider_calls=10,
        goal_documents_fetched=3,
        goal_llm_query_calls=2,
        goal_llm_verify_calls=4,
        goal_search_rounds=1,
        goal_soft_deadline_ms=30_000,
        goal_hard_deadline_ms=45_000,
    )
    tool = ResearchBudget()
    used = ResearchBudgetUsage(provider_calls=4, documents_fetched=1, llm_verify_calls=1)
    effective = effective_budget(tool, goal, used)
    assert effective.max_provider_calls == 6       # min(8, 10-4)
    assert effective.max_documents_fetched == 2    # min(4, 3-1)
    assert effective.max_llm_query_calls == 1      # min(1, 2-0)
    assert effective.max_llm_verify_calls == 3     # min(6, 4-1)
    assert effective.max_search_rounds == 1        # min(2, 1-0)


def test_effective_budget_never_goes_negative_and_clamps_deadlines():
    goal = GoalResearchBudget(goal_provider_calls=1, goal_soft_deadline_ms=10_000, goal_hard_deadline_ms=20_000)
    tool = ResearchBudget(max_provider_calls=8, soft_deadline_ms=30_000, hard_deadline_ms=45_000)
    effective = effective_budget(tool, goal, ResearchBudgetUsage(provider_calls=99))
    assert effective.max_provider_calls == 0
    assert effective.soft_deadline_ms == 10_000
    assert effective.hard_deadline_ms == 20_000


def test_web_research_metrics_and_per_source_spans(tmp_path, monkeypatch):
    backend = _FakeBackend(name="fake", hits=[_Hit()], error="")
    tool = _web_tool([backend])
    ctx, recorder = _ctx(tmp_path)
    monkeypatch.setattr("app.capabilities.web_research.fetch_page", _fake_fetch_ok)

    result = tool.execute(_request(), ctx=ctx)
    data = result.artifact.structured_data["research_metrics"]
    queries = result.artifact.structured_data["queries"]
    assert data["provider_calls"] == len(queries)
    assert data["provider_calls_by_source"] == [{"source": "fake", "calls": len(queries)}]
    assert data["documents_fetched"] == 1
    assert data["fetch_outcomes"] == [{"status": "OK", "count": 1}]
    assert data["llm_query_calls"] == 0
    assert data["llm_verify_calls"] == 0
    assert set(data["budget"]) >= {"goal_provider_calls", "goal_documents_fetched", "goal_hard_deadline_ms"}

    source_spans = [span for span in ctx.latency.spans if span.stage.startswith("web:source:")]
    assert len(source_spans) == data["provider_calls"]
    assert all(span.stage == "web:source:fake" for span in source_spans)
    assert any(span.stage == "web:rank" for span in ctx.latency.spans)
    assert any(span.stage == "web:cache" for span in ctx.latency.spans)

    recorder.write()
    records = json.loads(recorder.path.read_text())
    metric_records = [record for record in records if record["stage"] == "research_metrics"]
    assert len(metric_records) == 1
    recorded = metric_records[0]["data"]
    assert recorded["provider_calls"] == len(queries)
    assert recorded["provider_calls_by_source"] == [{"source": "fake", "calls": len(queries)}]


def test_research_metrics_redaction_omits_unknown_fields(tmp_path):
    recorder = DiagnosticRecorder(tmp_path / "diag.json")
    recorder.record(
        "research_metrics",
        provider_calls=3,
        provider_calls_by_source=[{"source": "wikipedia", "calls": 3}],
        fetch_outcomes=[{"status": "OK", "count": 2}],
        body="private page body text",
        url="https://example.com/private",
        api_key="super-secret",
    )
    recorder.write()
    recorded = json.loads(recorder.path.read_text())[0]["data"]
    assert recorded["provider_calls"] == 3
    assert recorded["provider_calls_by_source"] == [{"source": "wikipedia", "calls": 3}]
    assert recorded["fetch_outcomes"] == [{"status": "OK", "count": 2}]
    assert recorded["api_key"] == "***"
    assert recorded["body"] == "<omitted>"
    assert recorded["url"] == "<omitted>"


# ------------------------------------------------------------------------ M1-T2 tests


def test_source_outcome_taxonomy_values():
    assert {item.value for item in SourceOutcome} == {
        "OK", "EMPTY", "RATE_LIMITED", "QUOTA_EXCEEDED", "AUTH_REQUIRED", "BLOCKED", "TIMEOUT", "ERROR",
    }


@pytest.mark.parametrize(
    "error,expected",
    [
        ("HTTPStatusError: Client error '403 Forbidden'", SourceOutcome.BLOCKED),
        ("401 Unauthorized", SourceOutcome.AUTH_REQUIRED),
        ("429 Too Many Requests", SourceOutcome.RATE_LIMITED),
        ("ReadTimeout: timed out", SourceOutcome.TIMEOUT),
        ("ConnectError: connection refused", SourceOutcome.ERROR),
    ],
)
def test_classify_error_text(error, expected):
    assert classify_search_result(hits=[], error=error) == expected


def test_classify_raised_timeout():
    assert classify_search_result(hits=[], error="", raised=TimeoutError("timed out")) == SourceOutcome.TIMEOUT


def test_classify_ok_and_empty():
    assert classify_search_result(hits=[_Hit()], error="") == SourceOutcome.OK
    assert classify_search_result(hits=[], error="") == SourceOutcome.EMPTY


def test_dead_provider_is_not_no_results():
    tool = _web_tool([_FakeBackend(name="wiki", error="HTTPStatusError: 403 Forbidden")])
    result = tool.execute(_request())
    assert result.outcome.code == "SEARCH_BACKEND_UNAVAILABLE"
    outcome = result.artifact.structured_data["source_outcomes"][0]
    assert outcome["source"] == "wiki"
    assert outcome["status"] in {"AUTH_REQUIRED", "BLOCKED"}


def test_timeout_provider_is_typed():
    tool = _web_tool([_FakeBackend(name="wiki", raises=TimeoutError("timed out"))])
    result = tool.execute(_request())
    assert result.outcome.code == "SEARCH_BACKEND_UNAVAILABLE"
    assert result.artifact.structured_data["source_outcomes"][0]["status"] == "TIMEOUT"


def test_all_providers_empty_is_no_results():
    tool = _web_tool([_FakeBackend(name="a"), _FakeBackend(name="b")])
    result = tool.execute(_request())
    assert result.outcome.code == "NO_RESULTS"
    assert {row["status"] for row in result.artifact.structured_data["source_outcomes"]} == {"EMPTY"}


def test_partial_failure_preserves_successful_hits(monkeypatch):
    dead = _FakeBackend(name="dead", error="HTTPStatusError: 403 Forbidden")
    live = _FakeBackend(name="live", hits=[_Hit()])
    tool = _web_tool([dead, live])
    monkeypatch.setattr("app.capabilities.web_research.fetch_page", _fake_fetch_ok)
    result = tool.execute(_request())
    assert result.outcome.status == "SUCCESS"
    statuses = {row["source"]: row["status"] for row in result.artifact.structured_data["source_outcomes"]}
    assert statuses["dead"] in {"AUTH_REQUIRED", "BLOCKED"}
    assert statuses["live"] == "OK"


# ------------------------------------------------------------------------ M1-T4 tests


def test_source_descriptor_construction_and_validation():
    descriptor = SourceDescriptor(
        source_id="wikipedia",
        kind="ENCYCLOPEDIA",
        discovery=True,
        full_body=True,
        structured=False,
        historical=True,
        current=True,
        auth="none",
        trust_weight=0.85,
        latency_class="medium",
    )
    assert descriptor.source_id == "wikipedia"
    assert descriptor.trust_weight == 0.85
    with pytest.raises(ValueError):
        SourceDescriptor("bad", "NOT_A_KIND", True, True, False, False, False)
    with pytest.raises(ValueError):
        SourceDescriptor("bad", "ENCYCLOPEDIA", True, True, False, False, False, trust_weight=1.5)
    with pytest.raises(ValueError):
        SourceDescriptor("bad", "ENCYCLOPEDIA", True, True, False, False, False, auth="oauth")


def test_wikipedia_descriptor_advertises_capabilities():
    source = WikipediaSource(WebSearchConfig())
    descriptor = source.descriptor
    assert descriptor.source_id == "wikipedia"
    assert descriptor.historical is True
    assert descriptor.full_body is True
    assert descriptor.discovery is True
    assert descriptor.auth == "none"
    assert 0.55 < descriptor.trust_weight <= 1.0
    assert isinstance(source, SearchProvider)


def test_wikipedia_capability_probe_is_typed_not_raising(monkeypatch):
    source = WikipediaSource(WebSearchConfig())

    def boom(*args, **kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr(source._backend, "search", boom)
    assert source.available() == SourceOutcome.TIMEOUT

    monkeypatch.setattr(source._backend, "search", lambda *a, **k: [object()])
    assert source.available() == SourceOutcome.OK

    monkeypatch.setattr(source._backend, "search", lambda *a, **k: [])
    source._backend.last_error = ""
    assert source.available() == SourceOutcome.EMPTY


def test_web_artifact_records_source_descriptors(monkeypatch):
    tool = _web_tool([_FakeBackend(name="fake", hits=[_Hit()])])
    monkeypatch.setattr("app.capabilities.web_research.fetch_page", _fake_fetch_ok)
    result = tool.execute(_request())
    sources = {item["source_id"]: item for item in result.artifact.structured_data["sources"]}
    assert "wikipedia" in sources
    assert sources["wikipedia"]["historical"] is True
    assert sources["wikipedia"]["auth"] == "none"


# ------------------------------------------------------------------------ M1-T6 tests


def test_body_hash_is_stable_and_content_sensitive():
    body = "Shohei Ohtani hit 55 home runs in 2025."
    assert body_hash(body) == body_hash(body)
    assert body_hash(body) != body_hash(body + " He also stole 59 bases.")
    metadata = body_metadata(body)
    assert metadata == {"body_hash": body_hash(body), "extractor_version": EXTRACTOR_VERSION}


def test_extractor_version_mismatch_forces_reextraction():
    body = "stable body text"
    stale = {"body_hash": body_hash(body), "extractor_version": "old-extractor-v0"}
    assert can_reuse_offsets(stale, body) is False
    current = body_metadata(body)
    assert can_reuse_offsets(current, body) is True
    # A stale version must re-extract rather than reuse the stored offsets.
    calls = []

    def extract(raw_bytes):
        calls.append(raw_bytes)
        return body

    new_body, version = reextract(stale, b"raw-bytes", extract)
    assert calls == [b"raw-bytes"]
    assert new_body == body
    assert version == EXTRACTOR_VERSION
    assert can_reuse_offsets(body_metadata(new_body), new_body) is True


def test_document_body_version_metadata_and_offsets(monkeypatch):
    tool = _web_tool([_FakeBackend(name="fake", hits=[_Hit()])])
    monkeypatch.setattr("app.capabilities.web_research.fetch_page", _fake_fetch_ok)
    result = tool.execute(_request())
    data = result.artifact.structured_data
    document = data["documents"][0]
    assert document["body_hash"] == body_hash(document["body_text"])
    assert document["extractor_version"] == EXTRACTOR_VERSION
    assert document["raw_content_hash"] == document["content_hash"]
    for span in data["evidence_spans"]:
        assert document["body_text"][span["start"]:span["end"]] == span["text"]


def test_structured_record_round_trips_and_has_provenance_header():
    record = StructuredRecord(
        record_id="statsapi:660271:2025:hitting",
        source_id="statsapi",
        source_url="https://statsapi.mlb.com/api/v1/people/660271",
        as_of="2025-10-01T00:00:00Z",
        conversion_version="statsapi-record-v1",
        raw_fields={"homeRuns": 55, "season": "2025"},
    )
    from dataclasses import asdict

    restored = StructuredRecord(**asdict(record))
    assert restored == record
    body = record.render_body()
    assert body.startswith(record.provenance_header())
    for value in (record.source_id, record.record_id, record.as_of, record.source_url, record.conversion_version):
        assert value in record.provenance_header()
    assert structured_source_quality("statsapi") == "official_structured_record:statsapi"
    assert structured_location(record.record_id) == f"structured:{record.record_id}"


# ------------------------------------------------------------------------ M1-T5 tests


def _strike_proposition():
    return {
        "statement": "The 1994 Major League Baseball strike was a work stoppage over a salary cap.",
        "kind": "EXPLANATION",
        "subject": "1994 Major League Baseball strike",
        "time_scope": {"season": 1994},
        "required_information": "strike",
        "mandatory": True,
    }


_STRIKE_BODY = (
    "The 1994 Major League Baseball strike was a work stoppage that began on August 12, 1994. "
    "The strike was caused by a dispute over a proposed salary cap and revenue sharing. "
    "The strike led to the cancellation of the 1994 World Series and ended in 1995. "
) * 3


def test_wikipedia_shaped_body_yields_offsets_and_finding(monkeypatch):
    hit = _Hit(url="https://en.wikipedia.org/wiki/1994_strike", engine="wikipedia")
    tool = _web_tool([_FakeBackend(name="wikipedia", hits=[hit])])
    monkeypatch.setattr(
        "app.capabilities.web_research.fetch_page",
        lambda url, config: _fake_fetch_ok(url, config, text=_STRIKE_BODY),
    )
    result = tool.execute(_request(subjects=("1994 Major League Baseball strike",), propositions=[_strike_proposition()]))
    data = result.artifact.structured_data
    document = data["documents"][0]
    assert document["body_text"] == _STRIKE_BODY
    assert data["evidence_spans"]
    for span in data["evidence_spans"]:
        assert document["body_text"][span["start"]:span["end"]] == span["text"]
    assert data["evidence_findings"], "the existing verifier must produce a finding"
    assert any(f["document_id"] == document["id"] for f in data["evidence_findings"])


def test_page_without_subject_yields_no_support(monkeypatch):
    hit = _Hit(url="https://en.wikipedia.org/wiki/Flowers", engine="wikipedia")
    tool = _web_tool([_FakeBackend(name="wikipedia", hits=[hit])])
    monkeypatch.setattr(
        "app.capabilities.web_research.fetch_page",
        lambda url, config: _fake_fetch_ok(url, config, text="Roses and tulips are common garden flowers. " * 8),
    )
    result = tool.execute(_request(subjects=("1994 Major League Baseball strike",), propositions=[_strike_proposition()]))
    assert result.outcome.status == "NO_DATA"
    supported = [f for f in result.artifact.structured_data["evidence_findings"] if f["status"] == "SUPPORTED"]
    assert supported == []


def test_historical_explanation_prioritizes_wikipedia(monkeypatch):
    wiki_hit = _Hit(url="https://en.wikipedia.org/wiki/1994_strike", engine="wikipedia")
    feed_hit = _Hit(url="https://feed.example/1", title="recent news", engine="publisher_feeds")
    tool = _web_tool([
        _FakeBackend(name="publisher_feeds", hits=[feed_hit]),
        _FakeBackend(name="wikipedia", hits=[wiki_hit]),
    ])
    fetched: list[str] = []

    def fake_fetch(url, config):
        fetched.append(url)
        return _fake_fetch_ok(url, config, text=_STRIKE_BODY)

    monkeypatch.setattr("app.capabilities.web_research.fetch_page", fake_fetch)
    result = tool.execute(_request(subjects=("1994 Major League Baseball strike",), propositions=[_strike_proposition()]))
    assert fetched[0] == wiki_hit.url
    assert result.artifact.structured_data["source_plan"]["primary"] == "wikipedia"


def test_non_historical_does_not_apply_historical_source_plan(monkeypatch):
    """M1-T5 source plan stays round_robin for non-historical questions.

    M2-3 relationship-aware ranking is allowed to order the fetched candidates (a more
    trusted source with no distinguishing lexical match ranks first), so this test asserts
    the *source plan*, not the pre-M2 backend order.
    """
    wiki_hit = _Hit(url="https://en.wikipedia.org/wiki/Player", engine="wikipedia")
    feed_hit = _Hit(url="https://feed.example/1", title="recent news", engine="publisher_feeds")
    tool = _web_tool([
        _FakeBackend(name="publisher_feeds", hits=[feed_hit]),
        _FakeBackend(name="wikipedia", hits=[wiki_hit]),
    ])
    fetched: list[str] = []

    def fake_fetch(url, config):
        fetched.append(url)
        return _fake_fetch_ok(url, config, text="Shohei Ohtani had a strong season. " * 8)

    monkeypatch.setattr("app.capabilities.web_research.fetch_page", fake_fetch)
    proposition = {
        "statement": "Shohei Ohtani had a strong season.",
        "kind": "FACT",
        "subject": "Shohei Ohtani",
        "required_information": "season",
        "mandatory": True,
    }
    result = tool.execute(_request(subjects=("Shohei Ohtani",), propositions=[proposition]))
    assert result.artifact.structured_data["source_plan"]["primary"] == "round_robin"
    assert set(fetched) == {wiki_hit.url, feed_hit.url}


def test_judge_verifier_hard_gates_preserved():
    """The Evidence Verifier's frozen hard gates remain invariants.

    The M1 guard originally asserted that ``app/verification/evidence.py`` was byte-identical
    to the Phase 5B baseline. The WSA-01/WSA-02 milestone intentionally *extends* the
    verifier with typed temporal/assertion admission, so file identity is no longer the right
    invariant. The original safety gates are asserted behaviorally instead: admissibility,
    known-scope conflict, and no LLM upgrade past a hard conflict. ``app/verification/judge.py``,
    ``app/response/composer.py`` and ``app/verification/consistency.py`` remain intentionally
    unfrozen and are locked by their own suites.
    """
    from app.contracts.models import (
        AnswerProposition,
        EvidenceSource,
        EvidenceSpan,
        RetrievedDocument,
        VerificationStatus,
    )
    from app.verification.evidence import merge_verification, verify_span

    body = "Shohei Ohtani was traded in 2024."
    document = RetrievedDocument(
        id="doc-1", url="https://example.test/a", final_url="https://example.test/a",
        retrieved_at="2026-09-01T00:00:00Z", content_hash="hash-abc", body_text=body,
    )
    proposition = AnswerProposition(
        statement="the player was traded", kind="EVENT", subject="Shohei Ohtani",
        required_information="trade", time_scope={"season": 2024},
    )
    # A search snippet is never admissible body evidence.
    snippet = EvidenceSpan(
        document_id="doc-1", text="Shohei Ohtani traded", start=0, end=len("Shohei Ohtani traded"),
        source_type=EvidenceSource.SEARCH_SNIPPET,
    )
    assert verify_span(proposition, snippet, document, allow_llm=False).status != VerificationStatus.SUPPORTED
    # A known year conflict is a hard conflict and can never be upgraded by an LLM.
    conflict_text = "Shohei Ohtani was traded in 2019."
    conflict = EvidenceSpan(document_id="doc-1", text=conflict_text, start=0, end=len(conflict_text))
    finding = verify_span(
        proposition, conflict, document.model_copy(update={"body_text": conflict_text}), allow_llm=False
    )
    assert finding.hard_conflict is True
    assert finding.status != VerificationStatus.SUPPORTED
    merged, _verifier = merge_verification(
        VerificationStatus.INSUFFICIENT, VerificationStatus.SUPPORTED, finding.hard_reasons
    )
    assert merged == VerificationStatus.INSUFFICIENT


# ------------------------------------------------------------------- M1-T5 live probes


@pytest.mark.integration
def test_wikipedia_live_search_fetch_span_and_verify():
    """Real network: Wikipedia search -> page fetch -> body -> span -> existing verifier."""
    from app.contracts.models import AnswerProposition, EvidenceSpan, RetrievedDocument, VerificationStatus
    from app.verification.evidence import verify_span

    config = WebSearchConfig(user_agent=WebConfig().user_agent)
    source = WikipediaSource(config)
    probe = source.available()
    if probe != SourceOutcome.OK:
        pytest.skip(f"Wikipedia unavailable: {probe}")
    hits = source.search("1994 Major League Baseball strike", limit=3)
    assert hits, "Wikipedia search returned no results"
    page = source.fetch(hits[0].url)
    assert page.ok and page.text, f"Wikipedia page fetch failed: {page.error_code}"
    assert page.content_hash

    proposition = AnswerProposition(
        statement="The 1994 Major League Baseball strike was a work stoppage over a salary cap.",
        kind="EXPLANATION",
        subject="1994 Major League Baseball strike",
        time_scope={"season": 1994},
        required_information="strike",
    )
    document = RetrievedDocument(
        url=hits[0].url,
        final_url=page.final_url,
        provider="wikipedia",
        title=page.title,
        retrieved_at=page.retrieved_at,
        retrieval_status="OK",
        content_hash=page.content_hash,
        body_text=page.text,
    )
    from app.capabilities.web_search import evidence_spans

    spans = evidence_spans(page.text, "1994 Major League Baseball strike causes salary cap")
    assert spans, "the fetched body must yield evidence spans"
    findings = []
    for data in spans:
        span = EvidenceSpan(
            document_id=document.id,
            text=data["text"],
            start=data["start"],
            end=data["end"],
            source_type=data.get("source_type", "DOCUMENT_BODY"),
            retrieval_status="OK",
        )
        assert page.text[span.start : span.end] == span.text
        findings.append(verify_span(proposition, span, document, llm=None, allow_llm=False))
    assert findings
    assert any(f.status != VerificationStatus.IRRELEVANT for f in findings)


@pytest.mark.integration
def test_wikipedia_historical_full_path_live():
    """Real network + real Runtime: the historical question grounds a real Wikipedia body."""
    from app.config import get_config
    from app.contracts.models import GoalStatus
    from app.runtime.engine import Engine

    config = get_config()
    if not config.llm.configured:
        pytest.skip("LLM is not configured")
    source = WikipediaSource(WebSearchConfig(user_agent=config.web.user_agent))
    if source.available() != SourceOutcome.OK:
        pytest.skip("Wikipedia is unavailable in this environment")

    engine = Engine(config)
    result = engine.ask("it_m1_wiki", "解释一下1994年美国职棒大联盟罢工的原因和影响。", trace_level="off")
    web = [a for a in result.artifacts if a.kind == "WEB_EVIDENCE"]
    if not web:
        pytest.skip("planner did not produce a web research need")
    documents = [d for a in web for d in a.structured_data.get("documents", [])]
    wiki_docs = [d for d in documents if d.get("provider") == "wikipedia"]
    if not wiki_docs:
        pytest.skip("no Wikipedia document was retrieved in this run")
    ok_docs = [d for d in wiki_docs if d.get("retrieval_status") == "OK"]
    if not ok_docs:
        pytest.skip("no Wikipedia document body was retrieved in this run")
    document = ok_docs[0]
    body = document["body_text"]
    spans = [
        span
        for artifact in web
        for span in artifact.structured_data.get("evidence_spans", [])
        if span.get("document_id") == document["id"]
    ]
    assert spans, "a fetched Wikipedia body must yield evidence spans"
    for span in spans:
        assert body[span["start"] : span["end"]] == span["text"]
    findings = [f for artifact in web for f in artifact.structured_data.get("evidence_findings", [])]
    assert findings, "the existing verifier must produce at least one finding"
    assert result.projection.goal_status in {GoalStatus.COMPLETE, GoalStatus.LIMITED}


# ------------------------------------------------------------------------ M1-T7 tests


def _descriptor(source_id, kind, trust_weight, **overrides):
    values = {
        "source_id": source_id,
        "kind": kind,
        "discovery": True,
        "full_body": True,
        "structured": False,
        "historical": True,
        "current": True,
        "trust_weight": trust_weight,
    }
    values.update(overrides)
    return SourceDescriptor(**values)


def test_trust_weight_direction_is_monotone():
    from app.web.ranking import rank_by_trust, trust_score

    official = _descriptor("statsapi", "STRUCTURED", 1.00, full_body=False, structured=True)
    encyclopedia = _descriptor("wikipedia", "ENCYCLOPEDIA", 0.85)
    publisher = _descriptor("publisher_feeds", "PUBLISHER_FEEDS", 0.55)
    open_web = _descriptor("broad_search", "BROAD_SEARCH", 0.25)
    discovery = _descriptor("google_news", "PUBLISHER_FEEDS", 0.00, full_body=False)

    shuffled = [discovery, open_web, publisher, encyclopedia, official]
    ranked = [descriptor.source_id for descriptor in rank_by_trust(shuffled)]
    assert ranked == ["statsapi", "wikipedia", "publisher_feeds", "broad_search", "google_news"]
    assert trust_score(official) > trust_score(encyclopedia) > trust_score(publisher) > trust_score(open_web)
    assert trust_score(open_web) > trust_score(discovery)


@pytest.mark.parametrize(
    "error,expected",
    [
        ("403 Forbidden", SourceOutcome.BLOCKED),
        ("429 Too Many Requests", SourceOutcome.RATE_LIMITED),
        ("ReadTimeout: timed out", SourceOutcome.TIMEOUT),
        ("", SourceOutcome.EMPTY),
    ],
)
def test_source_outcome_classification_matrix(error, expected):
    assert classify_search_result(hits=[], error=error) == expected


def test_source_outcome_all_fail_matrix():
    tool = _web_tool([
        _FakeBackend(name="forbidden", error="403 Forbidden"),
        _FakeBackend(name="slow", raises=TimeoutError("timed out")),
    ])
    result = tool.execute(_request())
    assert result.outcome.code == "SEARCH_BACKEND_UNAVAILABLE"
    statuses = {row["source"]: row["status"] for row in result.artifact.structured_data["source_outcomes"]}
    assert statuses == {"forbidden": "BLOCKED", "slow": "TIMEOUT"}
