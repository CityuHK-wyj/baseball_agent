"""Phase 5B Closure regression tests.

Deterministic adversarial fixtures for the independently confirmed Phase 5B defects:

1. search titles cannot impersonate fetched body evidence;
2. known year conflicts block SUPPORTED;
3. known game-population conflicts block SUPPORTED;
4. an LLM verifier cannot override deterministic hard conflicts;
5. every persisted EvidenceFinding resolves to a persisted EvidenceSpan;
6. Judge coverage is driven by mandatory Requirements, not Artifact count;
7. supported statistics + unsupported explanation -> partial completion;
8. a missing verified result cannot keep a false COMPLETE;
9. missing *optional* material must not downgrade COMPLETE;
10. the consistency check does not duplicate Evidence Verifier semantic work;
11. diagnostics redact credentials while preserving token telemetry;
12. Phase 3/4/5A safety invariants remain intact.

No network is used here; real DB/LLM/Web evidence is exercised separately in
``tests/test_integration_live.py``.
"""

from __future__ import annotations
from tests.m212_receipt_fixture import with_receipt

import json
import stat
from types import SimpleNamespace

import pytest

from app.capabilities.web_research import WebResearchTool
from app.capabilities.web_search import FetchedPage, SSRFError, assert_safe_url
from app.config import WebConfig
from tools.requirement_ancestry_fixture import Goal, root_goal
from app.contracts.models import (
    AnswerProjection,
    AnswerProposition,
    Artifact,
    Claim,
    ConsistencyStatus,
    CoverageState,
    EvidenceSpan,
    GoalStatus,
    PlannerNeed,
    PropositionKind,
    RequirementCompletion,
    RequirementKind,
    RetrievedDocument,
    Scope,
    ToolRequest,
    UserRequirement,
    VerificationStatus,
)
from app.observe.diagnostics import DiagnosticRecorder
from app.verification.consistency import check_status_consistency, repair_answer
from app.verification.evidence import verify_span
from app.verification.judge import Judge
from app.verification.propositions import build_propositions

# --------------------------------------------------------------------------- helpers


class _FakeLLM:
    configured = True

    def __init__(self, status: str = "SUPPORTED") -> None:
        self.status = status
        self.calls = 0

    def complete_json(self, *, role: str = "response", system: str = "", user: str = "", **kwargs):
        from tests.assessment_fixtures import grounding_receipts, passage_from_user, reviewer_reply
        reply = reviewer_reply(system)
        self.calls += 1
        if reply is not None:
            return reply
        passage = passage_from_user(user)
        return SimpleNamespace(
            data={"status": self.status, "establishes": "the passage establishes it", "fact": {"subject": "Shohei Ohtani", "metric": "trade", "value": "", "scope": "December"}, "grounding": grounding_receipts(passage), "rationale": "fake"}
        )

    def complete_text(self, **kwargs):
        self.calls += 1
        return ("fake answer", 1, "fake")


def _prop(
    statement: str,
    required: str,
    *,
    subject: str = "Shohei Ohtani",
    kind: PropositionKind = PropositionKind.EXPLANATION,
    time_scope: dict | None = None,
    population: str | None = None,
) -> AnswerProposition:
    return AnswerProposition(
        statement=statement,
        kind=kind,
        subject=subject,
        required_information=required,
        time_scope=time_scope or {},
        population=population,
    )


def _document(*, doc_id: str = "doc-1", title: str = "title", url: str = "https://example.com/a") -> RetrievedDocument:
    return RetrievedDocument(
        id=doc_id, url=url, final_url=url, title=title,
        retrieved_at="2026-09-01T00:00:00Z", content_hash="hash", retrieval_status="OK",
    )


class _Hit:
    def __init__(self, url: str, title: str) -> None:
        self.url = url
        self.title = title
        self.snippet = ""
        self.engine = "fixture"


class _Backend:
    name = "fixture"

    def __init__(self, hits) -> None:
        self._hits = list(hits)
        self.last_error = ""

    def search(self, query, *, limit=5, language="en"):
        return list(self._hits)


def _run_web(monkeypatch, pages, props, *, objective="trade role change", subjects=("Shohei Ohtani",)):
    """pages: {url: (title, text, ok)} — deterministic replayable fixtures."""
    hits = [_Hit(url, pages[url][0]) for url in pages]
    tool = WebResearchTool(WebConfig(broad_search=False), llm=None)
    tool.backends = [_Backend(hits)]

    def fake_fetch(url, config):
        title, text, ok = pages[url]
        return FetchedPage(
            url=url, final_url=url, title=title, text=text,
            retrieved_at="2026-09-01T00:00:00Z", ok=ok,
            error="" if ok else "boom", error_code="" if ok else "FETCH_FAILED",
            content_hash="hash-1",
        )

    monkeypatch.setattr("app.capabilities.web_research.fetch_page", fake_fetch)
    request = ToolRequest(
        id="tr_closure", need_id="n_web", capability="web_research", objective=objective,
        inputs={
            "entity_names": list(subjects),
            "objective": objective,
            "user_wording": "",
            "propositions": [p.model_dump(exclude={"coverage"}) for p in props],
        },
    )
    return tool.execute(request)


def _needs(requirement_id: str) -> list[PlannerNeed]:
    return [PlannerNeed(id="n_web", objective="research", capability="web_research", requires=[requirement_id])]


def test_unrelated_definition_cannot_complete_a_historical_explanation():
    requirement = UserRequirement(
        id="r1", kind=RequirementKind.ANALYSIS,
        statement="explain the causes and consequences of the 1994 MLB strike",
        value={"objective": "explain the causes and consequences of the 1994 MLB strike"},
        completion=RequirementCompletion.EXPLANATION,
    )
    goal = Goal(statement="1994 strike", raw_query="explain the 1994 MLB strike", requirements=[requirement])
    goal.propositions = build_propositions(goal)
    # A generic pitch definition shares the word "strike" but proves nothing about 1994.
    knowledge = Artifact(
        id="art_k", kind="KNOWLEDGE_ITEM",
        structured_data={"items": [{"id": "K", "title": "Strike (baseball)", "summary": "A strike is a pitch thrown in the zone.", "aliases": ["strike"]}]},
        text_content="A strike is a pitch thrown in the zone.",
        actual_scope=Scope(source="SHARED_KNOWLEDGE"), need_id="n_k",
    )
    needs = [PlannerNeed(id="n_k", objective="knowledge", capability="shared_knowledge", requires=["r1"])]
    projection = Judge().evaluate(goal, [knowledge], needs)
    assert projection.goal_status != GoalStatus.COMPLETE
    assert not any(p.coverage == CoverageState.SATISFIED for p in projection.propositions if p.mandatory)


# ============================================================= 1. title <-> passage


def test_search_title_cannot_impersonate_fetched_evidence(monkeypatch):
    proposition = _prop(
        "explain the trade role change",
        "trade role change",
        time_scope={"season": 2024},
        kind=PropositionKind.EXPLANATION,
    )
    pages = {
        "https://example.com/title-only": (
            "Shohei Ohtani 2024 trade role change",
            "This body discusses an unrelated topic and establishes none of those facts. " * 4,
            True,
        )
    }
    result = _run_web(monkeypatch, pages, [proposition], objective="trade role change")
    artifact = result.artifact
    assert artifact is not None
    statuses = [f["status"] for f in artifact.structured_data["evidence_findings"]]
    assert VerificationStatus.SUPPORTED.value not in statuses
    # The body does not name the subject, so no span is admitted as support.
    assert artifact.structured_data["evidence_spans"]


def test_title_tokens_do_not_satisfy_required_information(monkeypatch):
    proposition = _prop(
        "explain the trade role change",
        "trade role change",
        time_scope={"season": 2024},
        kind=PropositionKind.EXPLANATION,
    )
    pages = {
        "https://example.com/partial": (
            "Shohei Ohtani 2024 trade role change",
            "Shohei Ohtani had a strong season and helped his team. " * 4,
            True,
        )
    }
    result = _run_web(monkeypatch, pages, [proposition], objective="trade role change")
    statuses = [f["status"] for f in result.artifact.structured_data["evidence_findings"]]
    assert VerificationStatus.SUPPORTED.value not in statuses
    # required tokens ("trade", "role", "change") are absent from the passage.
    for finding in result.artifact.structured_data["evidence_findings"]:
        assert finding["checks"]["matched_tokens"] == []


def test_persisted_spans_declare_real_source(monkeypatch):
    proposition = _prop("explain the trade", "trade", kind=PropositionKind.EXPLANATION)
    pages = {
        "https://example.com/ok": (
            "Ohtani news",
            "Shohei Ohtani was traded in a blockbuster deal. " * 6,
            True,
        )
    }
    result = _run_web(monkeypatch, pages, [proposition], objective="trade")
    spans = result.artifact.structured_data["evidence_spans"]
    assert spans
    for span in spans:
        assert span["source_type"] == "DOCUMENT_BODY"
        assert span["retrieval_status"] == "OK"


# ================================================= 2/3. deterministic hard constraints


def test_known_year_conflict_blocks_supported():
    proposition = _prop("home runs", "home runs", time_scope={"season": 2024}, kind=PropositionKind.VALUE)
    finding = verify_span(proposition, EvidenceSpan(document_id="doc-1", text="Shohei Ohtani hit home runs in the 2023 regular season.", start=0, end=60), _document())
    assert finding.status == VerificationStatus.INSUFFICIENT
    assert finding.checks["time_state"] == "MISMATCH"
    assert finding.hard_conflict is True


def test_known_population_conflict_blocks_supported():
    proposition = _prop("postseason home runs", "home runs", population="POSTSEASON", kind=PropositionKind.VALUE)
    finding = verify_span(proposition, EvidenceSpan(document_id="doc-1", text="Shohei Ohtani hit home runs in the regular season.", start=0, end=50), _document())
    assert finding.status == VerificationStatus.INSUFFICIENT
    assert finding.checks["population_state"] == "MISMATCH"
    assert finding.hard_conflict is True


def test_missing_scope_metadata_is_unknown_not_a_match():
    proposition = _prop("home runs", "home runs", time_scope={"season": 2024}, kind=PropositionKind.VALUE)
    finding = verify_span(proposition, EvidenceSpan(document_id="doc-1", text="Shohei Ohtani hit home runs.", start=0, end=30), _document())
    assert finding.checks["time_state"] == "UNKNOWN"
    assert finding.status == VerificationStatus.INSUFFICIENT
    # UNKNOWN (missing metadata) is distinct from MISMATCH (a known conflict): it blocks an
    # LLM upgrade but is not a conflict.
    assert finding.hard_conflict is False
    assert finding.hard_reasons


# ========================================================= 4. LLM cannot override


def test_llm_cannot_override_deterministic_conflict():
    from app.verification.evidence import merge_verification

    proposition = _prop("home runs", "home runs", time_scope={"season": 2024}, population="POSTSEASON", kind=PropositionKind.VALUE)
    llm = _FakeLLM(status="SUPPORTED")
    finding = verify_span(
        proposition,
        EvidenceSpan(document_id="doc-1", text="Shohei Ohtani hit home runs in the 2023 regular season.", start=0, end=60),
        _document(),
        llm=llm,
        allow_llm=True,
    )
    # A known conflict is frozen: the semantic verifier is not even consulted.
    assert llm.calls == 0
    assert finding.status != VerificationStatus.SUPPORTED
    assert finding.hard_conflict is True
    assert finding.deterministic_status == VerificationStatus.INSUFFICIENT
    # The merge rule itself can never upgrade a blocked finding.
    merged, verifier = merge_verification(
        VerificationStatus.INSUFFICIENT, VerificationStatus.SUPPORTED, finding.hard_reasons
    )
    assert merged == VerificationStatus.INSUFFICIENT
    assert verifier == "deterministic"


def test_llm_can_refine_when_hard_gate_passes():
    proposition = _prop("the trade happened", "trade", kind=PropositionKind.EVENT)
    llm = _FakeLLM(status="SUPPORTED")
    finding = verify_span(
        proposition,
        EvidenceSpan(document_id="doc-1", text="Shohei Ohtani was traded in December.", start=0, end=len("Shohei Ohtani was traded in December.")),
        _document().model_copy(update={"body_text": "Shohei Ohtani was traded in December."}),
        llm=llm,
        allow_llm=True,
    )
    assert finding.hard_conflict is False
    assert finding.status == VerificationStatus.SUPPORTED
    assert finding.verifier == "llm"


# ===================================================== 5. evidence lineage integrity


def test_every_persisted_finding_resolves_to_a_span(monkeypatch):
    proposition = _prop("explain the trade", "trade", kind=PropositionKind.EXPLANATION)
    pages = {
        "https://example.com/ok": (
            "Ohtani trade",
            "Shohei Ohtani was traded in a blockbuster deal that changed the roster. " * 6,
            True,
        )
    }
    result = _run_web(monkeypatch, pages, [proposition], objective="trade")
    data = result.artifact.structured_data
    span_by_id = {span["id"]: span for span in data["evidence_spans"]}
    doc_ids = {doc["id"] for doc in data["documents"]}
    assert span_by_id and data["evidence_findings"]
    for finding in data["evidence_findings"]:
        span = span_by_id.get(finding["span_id"])
        assert span is not None, "a finding must resolve to a persisted span"
        assert span["document_id"] == finding["document_id"]
        assert span["document_id"] in doc_ids
        assert span["source_type"] == "DOCUMENT_BODY"
        assert span["end"] - span["start"] == len(span["text"])


def test_unresolved_finding_is_not_admitted_by_judge():
    proposition = _prop("explain the trade", "trade", kind=PropositionKind.EXPLANATION)
    requirement = UserRequirement(
        id="r1", kind=RequirementKind.ANALYSIS, statement="trade",
        value={"objective": "trade"}, completion=RequirementCompletion.EXPLANATION,
    )
    goal = Goal(statement="trade", raw_query="trade", requirements=[requirement])
    goal.propositions = [proposition]
    proposition.requirement_id = "r1"
    web = Artifact(
        id="art_web", kind="WEB_EVIDENCE",
        structured_data={
            "proposition_verification": True,
            "evidence_spans": [],
            "documents": [],
            "evidence_findings": [
                {"id": "ef1", "proposition_id": proposition.id, "document_id": "ghost", "span_id": "s_missing",
                 "status": VerificationStatus.SUPPORTED.value, "verifier": "deterministic"}
            ],
        },
        actual_scope=Scope(source="WEB"), need_id="n_web",
    )
    projection = Judge().evaluate(goal, [web], _needs("r1"))
    assert projection.goal_status != GoalStatus.COMPLETE
    prop = next(p for p in projection.propositions if p.id == proposition.id)
    assert prop.coverage != CoverageState.SATISFIED


# ======================================================== 6. mandatory coverage, not counts


def test_mandatory_coverage_not_artifact_count():
    requirements = [
        UserRequirement(id="r1", kind=RequirementKind.ANALYSIS, statement="stat", value={"objective": "stat"}, completion=RequirementCompletion.GROUNDED),
        UserRequirement(id="r2", kind=RequirementKind.ANALYSIS, statement="cause", value={"objective": "cause"}, completion=RequirementCompletion.EXPLANATION),
    ]
    goal = Goal(statement="g", raw_query="g", requirements=requirements)
    goal.propositions = build_propositions(goal)
    stat = Artifact(
        id="art_db", kind="DATABASE_RESULT",
        structured_data={"rows": [{"avg": 0.3}], "row_count": 1},
        text_content="avg\n0.3", actual_scope=Scope(season=2024, source="POSTGRES"), need_id="n_db",
    )
    unrelated = Artifact(
        id="art_web", kind="WEB_EVIDENCE",
        structured_data={
            "proposition_verification": True,
            "evidence_spans": [{"id": "s1", "document_id": "d1", "text": "A generic definition of a stat.", "start": 0, "end": 30, "source_type": "DOCUMENT_BODY", "retrieval_status": "OK"}],
            "documents": [{"id": "d1", "url": "https://example.com", "retrieval_status": "OK"}],
            "evidence_findings": [],
        },
        actual_scope=Scope(source="WEB"), need_id="n_web",
    )
    needs = [
        PlannerNeed(id="n_db", objective="stats", capability="local_analytics", requires=["r1"]),
        PlannerNeed(id="n_web", objective="cause", capability="web_research", requires=["r2"]),
    ]
    projection = Judge().evaluate(goal, [stat, unrelated], needs)
    assert projection.goal_status == GoalStatus.LIMITED
    cause = next(p for p in projection.propositions if p.requirement_id == "r2")
    assert cause.coverage != CoverageState.SATISFIED


# ===================================================== 7. statistics vs explanation


def test_supported_statistic_with_unsupported_explanation_is_partial():
    requirements = [
        UserRequirement(id="r_stat", kind=RequirementKind.ANALYSIS, statement="Report batter batting average", value={"objective": "Report batter batting average"}, completion=RequirementCompletion.GROUNDED),
        UserRequirement(id="r_cause", kind=RequirementKind.ANALYSIS, statement="why", value={"objective": "explain why"}, completion=RequirementCompletion.EXPLANATION),
    ]
    goal = Goal(statement="g", raw_query="Report batter batting average in 2024; Explain why.", requirements=requirements)
    goal.propositions = build_propositions(goal)
    db = Artifact(
        id="art_db", kind="DATABASE_RESULT",
        structured_data={"rows": [{"avg": 0.3}], "row_count": 1},
        text_content="avg\n0.3", actual_scope=Scope(season=2024, source="POSTGRES"), need_id="n_db",
    )
    needs = [
        PlannerNeed(id="n_db", objective="stats", capability="local_analytics", requires=["r_stat"]),
        PlannerNeed(id="n_web", objective="cause", capability="web_research", requires=["r_cause"]),
    ]
    goal.requirements.append(UserRequirement(id="r_bound_season",kind=RequirementKind.SEASON,statement="2024",value=2024))
    root_goal(goal)
    goal.propositions = build_propositions(goal)
    db = with_receipt(db)
    projection = Judge().evaluate(goal, [db], needs)
    assert projection.claims, "the supported statistical claim must survive"
    assert projection.goal_status == GoalStatus.LIMITED
    assert not any(p.requirement_id == "r_cause" and p.coverage == CoverageState.SATISFIED for p in projection.propositions)


# ==================================================== 8/9. consistency: retraction/optional


def _complete_projection(*propositions: AnswerProposition, claims=None) -> AnswerProjection:
    return AnswerProjection(
        goal_id="g1", goal_status=GoalStatus.COMPLETE,
        claims=list(claims or []), propositions=list(propositions),
    )


def test_mandatory_result_retracted_cannot_keep_complete():
    proposition = AnswerProposition(
        id="prop1", statement="trade investigation", kind=PropositionKind.GROUNDED,
        required_information="trade and OPS change", mandatory=True, coverage=CoverageState.SATISFIED,
    )
    claim = Claim(id="c1", text="The player was traded; OPS went from .750 to .900.", proposition_ids=["prop1"])
    goal = Goal(statement="trade", raw_query="trade")
    projection = _complete_projection(proposition, claims=[claim])
    consistency = check_status_consistency(goal, projection, "I cannot provide the result; the data are unavailable.")
    assert consistency.status == ConsistencyStatus.DOWNGRADED
    assert "prop1" in consistency.retracted_proposition_ids


def test_optional_gap_does_not_downgrade_complete():
    mandatory = AnswerProposition(
        id="prop1", statement="main result", kind=PropositionKind.GROUNDED,
        required_information="trade and OPS change", mandatory=True, coverage=CoverageState.SATISFIED,
    )
    optional = AnswerProposition(
        id="prop2", statement="optional color", kind=PropositionKind.GROUNDED,
        required_information="clubhouse reaction", mandatory=False, coverage=CoverageState.UNSUPPORTED,
    )
    claim = Claim(id="c1", text="The player was traded; OPS went from .750 to .900.", proposition_ids=["prop1"])
    goal = Goal(statement="trade", raw_query="trade")
    projection = _complete_projection(mandatory, optional, claims=[claim])
    consistency = check_status_consistency(
        goal, projection, "The player was traded and OPS moved from .750 to .900. Clubhouse reaction was not found."
    )
    assert check_status_consistency(goal, projection, repair_answer(
        projection, "The player was traded and OPS moved from .750 to .900. Clubhouse reaction was not found.",
        consistency.retracted_proposition_ids)).status == ConsistencyStatus.CONSISTENT


def test_unrelated_limitation_does_not_downgrade_expressed_result():
    proposition = AnswerProposition(
        id="prop1", statement="trade investigation", kind=PropositionKind.GROUNDED,
        required_information="trade and OPS change", mandatory=True, coverage=CoverageState.SATISFIED,
    )
    claim = Claim(id="c1", text="The player was traded; OPS went from .750 to .900.", proposition_ids=["prop1"])
    goal = Goal(statement="trade", raw_query="trade")
    projection = _complete_projection(proposition, claims=[claim])
    answer = (
        "The player was traded and his OPS changed from .750 to .900.\n"
        "There is no evidence for an injury explanation, which was not requested."
    )
    consistency = check_status_consistency(goal, projection, answer)
    assert check_status_consistency(goal, projection, repair_answer(
        projection, answer, consistency.retracted_proposition_ids)).status == ConsistencyStatus.CONSISTENT


# ============================================ 10. consistency is not a second verifier


def test_consistency_does_not_call_an_llm_verifier():
    proposition = AnswerProposition(
        id="prop1", statement="trade", kind=PropositionKind.GROUNDED,
        required_information="trade", mandatory=True, coverage=CoverageState.SATISFIED,
    )
    claim = Claim(id="c1", text="The player was traded in 2024.", proposition_ids=["prop1"])
    goal = Goal(statement="trade", raw_query="trade")
    projection = _complete_projection(proposition, claims=[claim])
    llm = _FakeLLM(status="SUPPORTED")
    consistency = check_status_consistency(goal, projection, "The player was traded in 2024.", llm=llm)
    assert consistency.status == ConsistencyStatus.CONSISTENT
    assert llm.calls == 0, "consistency must not perform a second semantic evidence verification"


# ================================================================ 11. diagnostics


def test_diagnostics_redact_secrets_and_preserve_telemetry(tmp_path):
    path = tmp_path / "diag.json"
    recorder = DiagnosticRecorder(path)
    recorder.record(
        "web_research",
        url="https://x.test/page?api_key=ABC&token=XYZ&q=baseball",
        error="Authorization: Bearer PRIVATE-TOKEN",
        cookie="session=supersecret",
        api_key="raw-secret",
        subject_tokens=["shohei", "ohtani"],
        required_tokens=["trade"],
        matched_tokens=["trade"],
        prompt_tokens=120,
        completion_tokens=30,
    )
    # Private values embedded in arbitrary strings (user question/answer, page text,
    # exception text) must also be scrubbed, not only secret-named fields.
    recorder.record(
        "final_answer",
        answer="See https://x.test/?access_token=ANSWER-SECRET and Bearer ANSWER-BEARER-TOKEN",
        query="please use api_key=QUERY-SECRET for this",
    )
    assert recorder.write() == str(path)
    records = json.loads(path.read_text(encoding="utf-8"))
    data = records[0]["data"]
    assert data["api_key"] == "***"
    assert "ABC" not in data["url"] and "XYZ" not in data["url"]
    assert "PRIVATE-TOKEN" not in data["error"]
    assert "supersecret" not in data["cookie"]
    # Legitimate token telemetry is preserved.
    assert data["subject_tokens"] == ["<omitted>", "<omitted>"]
    assert data["required_tokens"] == ["<omitted>"]
    assert data["matched_tokens"] == ["<omitted>"]
    assert data["prompt_tokens"] == 120
    answer_data = records[1]["data"]
    assert "ANSWER-SECRET" not in answer_data["answer"]
    assert "ANSWER-BEARER-TOKEN" not in answer_data["answer"]
    assert "QUERY-SECRET" not in answer_data["query"]


def test_diagnostics_files_are_private(tmp_path):
    path = tmp_path / "nested" / "diag.json"
    recorder = DiagnosticRecorder(path)
    recorder.record("goal", statement="x")
    assert recorder.write() == str(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


# ============================================ 12. inherited safety invariants


def test_phase3_4_5a_safety_invariants_remain():
    from app.runtime.engine import IngressError, validate_ingress

    with pytest.raises(IngressError):
        validate_ingress("")
    with pytest.raises(IngressError):
        validate_ingress("bad\x00nul")
    with pytest.raises(SSRFError):
        assert_safe_url("http://127.0.0.1/admin")
    with pytest.raises(SSRFError):
        assert_safe_url("file:///etc/passwd")
