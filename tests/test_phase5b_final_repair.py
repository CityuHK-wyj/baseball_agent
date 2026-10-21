"""Independent acceptance counterexamples; all provider responses here are fixtures."""
import json
from types import SimpleNamespace

import pytest

from app.contracts.models import *
from tools.requirement_ancestry_fixture import Goal
from app.verification.evidence import verify_span
from app.verification.judge import Judge
from app.verification.consistency import check_status_consistency, repair_answer
from app.observe.diagnostics import DiagnosticRecorder
from app.llm.client import LLMClient
from app.config import LLMConfig
from tests.assessment_fixtures import FixedAssessor, support_llm


def chain(text="Shohei Ohtani hit home runs in 2024.", llm=None):
    p = AnswerProposition(id="p", requirement_id="r", statement="home runs", subject="Shohei Ohtani",
                          required_information="home runs", kind=PropositionKind.GROUNDED, time_scope={"season": 2024})
    d = RetrievedDocument(id="d", url="https://example.com/source", retrieved_at="2026-09-27", content_hash="hash", text_excerpt=text)
    s = EvidenceSpan(id="s", document_id="d", text=text, start=0, end=len(text))
    f = verify_span(p, s, d, llm=llm, allow_llm=llm is not None)
    return p, d, s, f


def projection(p, d, s, f, documents):
    r = UserRequirement(id="r", kind=RequirementKind.ANALYSIS, statement="home runs", completion=RequirementCompletion.GROUNDED)
    g = Goal(statement="home runs", raw_query="home runs", requirements=[r], propositions=[p])
    a = Artifact(id="a", kind="WEB_EVIDENCE", need_id="n", actual_scope=Scope(source="WEB"), structured_data={
        "proposition_verification": True, "documents": documents, "evidence_spans": [s.model_dump(mode="json")],
        "evidence_findings": [f.model_dump(mode="json")], "findings": [{"document_id": d.id, "url": d.url,
        "grounded": True, "evidence_spans": [s.model_dump(mode="json")], "proposition_ids": [p.id],
        "proposition_findings": [f.model_dump(mode="json")]}]})
    return Judge().evaluate(g, [a], [PlannerNeed(id="n", objective="home runs", capability="web_research", requires=["r"])])


@pytest.mark.parametrize("defect", ["missing", "failed", "body", "offset", "foreign", "finding_scope"])
def test_references_fail_closed(defect):
    p, d, s, f = chain()
    docs = [d.model_dump(mode="json")]
    if defect == "missing": docs = []
    if defect == "failed": docs[0]["retrieval_status"] = "FETCH_FAILED"
    if defect == "body": docs[0]["text_excerpt"] = "different body"
    if defect == "offset": s.start, s.end = 100, 100 + len(s.text)
    if defect == "foreign": docs[0]["id"] = "other-artifact-document"
    if defect == "finding_scope": f.hard_conflict = True
    out = projection(p, d, s, f, docs)
    assert out.goal_status != GoalStatus.COMPLETE
    assert not out.claims


def test_valid_chain():
    p, d, s, f = chain(llm=support_llm())
    assert f.status == VerificationStatus.SUPPORTED
    assert projection(p, d, s, f, [d.model_dump(mode="json")]).goal_status == GoalStatus.COMPLETE


@pytest.mark.parametrize("text", [
    "Shohei Ohtani watched Aaron Judge hit home runs in 2024.",
    "In 2024 Shohei Ohtani watched while Aaron Judge hit 25 home runs.",
])
def test_wrong_metric_owner(text):
    assert chain(text)[3].status != VerificationStatus.SUPPORTED


def test_comparison_preserves_attribution():
    text = "In 2024 Shohei Ohtani hit 25 home runs, while Aaron Judge hit 30 home runs."
    assert chain(text, llm=support_llm())[3].status == VerificationStatus.SUPPORTED


def test_comparison_with_and_keeps_both_subjects():
    p, d, s, _ = chain("In 2024 Shohei Ohtani hit 25 home runs and Aaron Judge hit 30 home runs.")
    p.subject = "Aaron Judge"
    assert verify_span(p, s, d, llm=support_llm(subject="Aaron Judge")).status == VerificationStatus.SUPPORTED


def test_restricted_research_is_explicit_and_does_not_enable_user_text(tmp_path):
    import stat
    rec = DiagnosticRecorder(tmp_path / "diag.json", research_content=True)
    rec.record("research", query="alice@example.com private", documents=[{"body_text": "public article api_key=SECRET"}])
    rec.write()
    raw = rec.path.read_text()
    # Bounded closure disables arbitrary body capture even in restricted mode.
    assert "public article" not in raw and "SECRET" not in raw and "alice@example.com" not in raw
    assert rec.path.parent.name == "restricted-research"
    assert stat.S_IMODE(rec.path.parent.stat().st_mode) == 0o700
    record = json.loads(raw)[0]
    import time
    assert 0 < record["expires_at_epoch"] - time.time() <= 86400


def test_retention_expires_only_versioned_records(tmp_path):
    import time
    expired = DiagnosticRecorder(tmp_path / "expired.json")
    expired.record("test", status="OK")
    expired.records[0]["expires_at_epoch"] = time.time() - 1
    expired.write()
    legacy = tmp_path / "legacy.json"
    # apply_patch-created fixture isn't needed: recorder can model a pre-policy record.
    old = DiagnosticRecorder(legacy)
    old.records = [{"stage": "old", "data": {}}]
    old.write()
    current = DiagnosticRecorder(tmp_path / "current.json")
    current.record("test", status="OK")
    current.write()
    assert not expired.path.exists() and legacy.exists() and current.path.exists()


def value_projection(text, mandatory=True):
    p = AnswerProposition(id="p", statement="required result", kind=PropositionKind.VALUE,
                          required_information="AVG OBP SLG", mandatory=mandatory, coverage=CoverageState.SATISFIED)
    c = Claim(text=text, proposition_ids=["p"])
    return Goal(statement="g", raw_query="g"), AnswerProjection(goal_id="g", goal_status=GoalStatus.COMPLETE, propositions=[p], claims=[c])


def test_mandatory_value_omission_and_repair():
    g, out = value_projection("The player hit 54 home runs.")
    answer = "I cannot provide the home-run total."
    check = check_status_consistency(g, out, answer)
    assert check.status != ConsistencyStatus.CONSISTENT
    assert check_status_consistency(g, out, repair_answer(out, answer, check.retracted_proposition_ids)).status == ConsistencyStatus.CONSISTENT


@pytest.mark.parametrize("answer,ok", [
    ("AVG .300", False), ("AVG .300, OBP .400, SLG .500", True),
    ("AVG .500, OBP .300, SLG .400", False),
])
def test_every_metric_value_role(answer, ok):
    g, out = value_projection("AVG .300, OBP .400, SLG .500")
    assert (check_status_consistency(g, out, answer).status == ConsistencyStatus.CONSISTENT) == ok


def test_before_after_omission():
    g, out = value_projection("OPS went from .750 to .900.")
    assert check_status_consistency(g, out, "OPS was .750.").status != ConsistencyStatus.CONSISTENT


def test_optional_value_omission():
    g, out = value_projection("AVG .300", mandatory=False)
    assert check_status_consistency(g, out, "No optional statistic.").status == ConsistencyStatus.CONSISTENT


def test_nested_private_diagnostics(tmp_path):
    path = tmp_path / "diag.json"
    rec = DiagnosticRecorder(path)
    rec.record("test", unexpected={"nested": ["alice@example.com medical note private", {"error": "private user history", "phone": 13812345678}]})
    rec.write()
    assert "alice@example.com" not in path.read_text()
    assert "private user history" not in path.read_text()
    assert "13812345678" not in path.read_text()


@pytest.mark.parametrize("usage", [SimpleNamespace(prompt_tokens=12, completion_tokens=7, total_tokens=19), None])
def test_provider_usage_reaches_recorder(tmp_path, usage):
    client = LLMClient(LLMConfig(api_key="fixture"))
    response = SimpleNamespace(model="actual-model", usage=usage, choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))])
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: response)))
    rec = DiagnosticRecorder(tmp_path / "usage.json")
    client.usage_sink = lambda **data: rec.record("llm_usage", **data)
    client.complete_json(role="semantic", system="fixture", user="fixture")
    client.complete_text(role="response", system="fixture", user="fixture")
    rec.write()
    records = json.loads(rec.path.read_text())
    assert len(records) == 2
    for record in records:
        data = record["data"]
        assert data["model"] == "actual-model"
        assert data["usage_available"] == (usage is not None)
        assert data["input_tokens"] == (12 if usage else None)
        assert data["output_tokens"] == (7 if usage else None)


def test_retry_usage_distinguishes_failed_attempt_from_measured_response():
    client = LLMClient(LLMConfig(api_key="fixture"))
    response = SimpleNamespace(model="actual-model", usage=SimpleNamespace(prompt_tokens=12, completion_tokens=7, total_tokens=19),
                               choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))])
    attempts = []
    def create(**kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("fixture network failure")
        return response
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    client.complete_json(role="semantic", system="fixture", user="fixture")
    assert len(client.usage_records) == 2
    assert client.usage_records[0]["usage_available"] is False
    assert client.usage_records[0]["total_tokens"] is None
    assert client.usage_records[1]["total_tokens"] == 19


@pytest.mark.parametrize("defect", ["span_content", "proposition", "artifact_scope", "nested_finding"])
def test_decision_is_bound_to_content_and_obligation(defect):
    p, d, s, f = chain()
    if defect == "span_content":
        s.text = "Aaron Judge hit home runs in 2024."
        s.end = len(s.text)
        d.text_excerpt = s.text
    if defect == "proposition": p.subject = "Aaron Judge"
    if defect == "artifact_scope": p.time_scope = {"season": 2023}
    if defect == "nested_finding": f.span_hash = "wrong"
    assert projection(p, d, s, f, [d.model_dump(mode="json")]).goal_status != GoalStatus.COMPLETE


def test_requested_value_must_belong_to_target():
    p, d, s, _ = chain("In 2024 Shohei Ohtani hit 20 home runs, while Aaron Judge hit 25 home runs.")
    p.statement = "Shohei Ohtani hit 25 home runs"
    assert verify_span(p, s, d).status != VerificationStatus.SUPPORTED


@pytest.mark.parametrize("season,population", [(2023, None), (2024, "POSTSEASON")])
def test_scope_conflicts_still_block_llm(season, population):
    p, d, s, _ = chain("Shohei Ohtani hit home runs in the 2024 regular season.")
    p.time_scope = {"season": season}
    p.population = population
    class LLM:
        configured = True
        def complete_json(self, **kwargs):
            pytest.fail("known conflicts must not invoke the LLM")
    assert verify_span(p, s, d, llm=LLM()).status != VerificationStatus.SUPPORTED


@pytest.mark.parametrize("text,population", [
    ("Shohei Ohtani hit 25 home runs in 2023, while Aaron Judge hit 30 home runs in 2024.", None),
    ("In 2024 Shohei Ohtani hit home runs in the regular season, while Aaron Judge hit home runs in the postseason.", "POSTSEASON"),
])
def test_scope_belongs_to_the_metric_owner(text, population):
    p, d, s, _ = chain(text)
    p.population = population
    assert verify_span(p, s, d, allow_llm=False).status != VerificationStatus.SUPPORTED


def test_values_without_commas_are_still_attributed():
    g, out = value_projection("AVG .300 OBP .400 SLG .500")
    assert check_status_consistency(g, out, "AVG .500 OBP .300 SLG .400").status != ConsistencyStatus.CONSISTENT


def test_unknown_attribution_is_not_supported():
    p, d, s, _ = chain("Shohei Ohtani and Aaron Judge discussed home runs in 2024.")
    assert verify_span(p, s, d, allow_llm=False).status != VerificationStatus.SUPPORTED


@pytest.mark.parametrize("fact", [None, {"subject": "Aaron Judge", "metric": "home runs", "value": "25"},
                                    {"subject": "Shohei Ohtani", "metric": "home runs", "value": "25"}])
def test_llm_must_identify_passage_fact(fact):
    p, d, s, _ = chain("Shohei Ohtani and Aaron Judge discussed home runs in 2024.")
    llm = FixedAssessor({"status": "SUPPORTED", "fact": fact})
    assert verify_span(p, s, d, llm=llm).status != VerificationStatus.SUPPORTED


def test_correct_llm_relation_can_support_complex_comparison():
    p, d, s, _ = chain("In 2024 Shohei Ohtani's 25 home runs exceeded Aaron Judge's 20 home runs.")
    fact = {"subject": "Shohei Ohtani", "metric": "home runs", "value": "25", "scope": "2024"}
    llm = FixedAssessor({"status": "SUPPORTED", "fact": fact})
    assert verify_span(p, s, d, llm=llm).status == VerificationStatus.SUPPORTED


@pytest.mark.parametrize("missing", ["value", "scope"])
def test_llm_numeric_support_requires_value_and_scope(missing):
    p, d, s, _ = chain("In 2024 home runs numbered 25 for Shohei Ohtani, compared with 30 for Aaron Judge.")
    p.kind = PropositionKind.VALUE
    fact = {"subject": "Shohei Ohtani", "metric": "home runs", "value": "25", "scope": "2024"}
    del fact[missing]
    llm = FixedAssessor({"status": "SUPPORTED", "fact": fact})
    assert verify_span(p, s, d, llm=llm).status != VerificationStatus.SUPPORTED


def test_answer_values_exclude_auxiliary_numbers_and_bind_scope():
    g, out = value_projection("table has auxiliary count 1234")
    out.propositions[0].requirement_id = "r"
    out.claims[0].answer_values = [{"requirement_id": "r", "entity": "Player Alpha", "metric": "avg", "value": .3,
                                  "season": 2024, "population": "REGULAR_SEASON", "unit": None}]
    assert check_status_consistency(g, out, "Player Alpha 2024 regular season AVG .300").status == ConsistencyStatus.CONSISTENT
    assert check_status_consistency(g, out, "Player Beta 2023 postseason AVG .300").status != ConsistencyStatus.CONSISTENT


def test_required_parent_controls_child_optionality():
    from app.verification.propositions import build_propositions
    for required in (True, False):
        req = UserRequirement(kind=RequirementKind.MEASURE, statement="AVG", required=required,
            value={"propositions": [{"statement": "AVG", "mandatory": not required}]})
        goal = Goal(statement="g", raw_query="g", requirements=[req])
        assert build_propositions(goal)[0].mandatory == required


def test_credentials_permissions_and_numeric_telemetry(tmp_path):
    import stat
    from app.observe.diagnostics import scrub_secret_values
    assert "ENCODEDSECRET" not in scrub_secret_values("https://x/?api%5Fkey=ENCODEDSECRET")
    path = tmp_path / "nested" / "diag.json"
    rec = DiagnosticRecorder(path)
    rec.record("llm_usage", api_key="secret", input_tokens=12, output_tokens=7, total_tokens=19)
    rec.write()
    data = json.loads(path.read_text())[0]["data"]
    assert data == {"api_key": "***", "input_tokens": 12, "output_tokens": 7, "total_tokens": 19}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
