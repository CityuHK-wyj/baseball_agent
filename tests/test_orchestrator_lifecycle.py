"""Bounded lifecycle decisions are distinct from evidence and presentation authority."""
from types import SimpleNamespace

import pytest

from app.contracts.models import (
    AnalysisRequest, AnswerProjection, AnswerProposition, Artifact, Claim, CoverageState,
    GoalStatus, NeedStatus, PlannerNeed, Reference, ReferenceType, RequirementAssessment,
    RequirementStatus, SemanticInterpretation,
)
from app.response.answer_contract import AnswerFact, AnswerPackage, build_answer_package, render_governed_package
from app.response.composition_contract import parse_composer_draft
from app.runtime.orchestrator import Orchestrator, OrchestratorAction
from app.verification.governed_state import GovernedStateError
from tests.conformance_fixtures import bound_review
from tests.governed_state_fixture import approved_snapshot
from tests.test_phase5b_evidence_completion import _engine_without_live_sources, _StubInterpreter


def _package():
    return AnswerPackage(goal_id="g", goal_status="COMPLETE", language="en",
                         facts=[AnswerFact(marker="S1", text="A hitter hit 35 home runs.", claim_id="c")])


def _draft(text):
    return parse_composer_draft({"statements": [{"kind": "FACTUAL", "text": text, "citations": ["S1"]}]})


def _review(package, draft):
    return bound_review({"statements": [{"index": 0, "epistemic": "FACTUAL", "approved_claim_backed": True,
                         "claim_markers": ["S1"], "unsupported_addition": "invented" in draft.statements[0].text}]},
                        package, draft)


def test_presentation_failure_regenerates_once_against_identical_package():
    lifecycle, package = Orchestrator(), _package()
    seen, feedbacks = [], []
    drafts = iter([_draft("A hitter hit 35 home runs and an invented comparison. [S1]"),
                   _draft("A hitter hit 35 home runs. [S1]")])
    def generate(supplied, feedback):
        seen.append(supplied)
        feedbacks.append(feedback)
        return next(drafts)
    def assess(supplied, draft):
        seen.append(supplied)
        return _review(supplied, draft)
    text = lifecycle.compose(package, generate, assess)
    assert text == "A hitter hit 35 home runs. [S1]"
    assert all(p is package for p in seen)
    assert feedbacks[0] == () and "UNSUPPORTED_FACTUAL" in feedbacks[1]
    assert not any("invented comparison" in f for f in feedbacks[1])
    assert [d.action for d in lifecycle.decisions] == [OrchestratorAction.REGENERATE_COMPOSER, OrchestratorAction.FINAL]
    assert lifecycle.composer_attempts == lifecycle.conformance_attempts == 2
    assert lifecycle.post_judge_replans == 0


def test_two_failed_drafts_exhaust_to_governed_fallback():
    lifecycle, package = Orchestrator(max_composer_attempts=999), _package()
    def generate(supplied, feedback):
        return _draft("An invented medical fact. [S1]")
    text = lifecycle.compose(package, generate, _review)
    assert text == render_governed_package(package)
    assert "invented" not in text
    assert lifecycle.composer_attempts == 2
    assert lifecycle.decisions[-1].action == OrchestratorAction.FALLBACK
    assert lifecycle.decisions[-1].attempt == 2
    assert lifecycle.decisions[-1].package_id == package.package_id


def test_semantic_assessment_outages_consume_attempts_and_fallback():
    lifecycle, package = Orchestrator(), _package()
    calls = []
    def assess(supplied, draft):
        calls.append(supplied.package_id)
        raise RuntimeError("owned outage")
    text = lifecycle.compose(package, lambda p, f: _draft("Invented medical fact."), assess)
    assert len(calls) == lifecycle.conformance_attempts == 2
    assert text == render_governed_package(package)


def _unresolved_projection():
    return AnswerProjection(goal_id="g", goal_status=GoalStatus.LIMITED,
        propositions=[AnswerProposition(id='p',requirement_id='r',statement='unresolved',kind='FACT',coverage='UNSUPPORTED')],
        requirement_assessments=[RequirementAssessment(requirement_id="r", status=RequirementStatus.UNSATISFIED,
                                                       coverage=CoverageState.UNSUPPORTED)]).frozen_snapshot()


@pytest.mark.parametrize("reasonable,budget", [(False, True), (True, False), (False, False)])
def test_replan_requires_both_reasonable_path_and_remaining_budget(reasonable, budget):
    lifecycle = Orchestrator()
    projection = _unresolved_projection()
    before = projection.model_dump_json()
    decision = lifecycle.post_judge_decision(projection, mandatory_requirement_ids={"r"},
                                             reasonable_path=reasonable, budget_remaining=budget)
    assert decision.action == OrchestratorAction.FINAL
    assert projection.goal_status == GoalStatus.LIMITED
    assert projection.model_dump_json() == before
    assert lifecycle.post_judge_replans == 0


def test_post_judge_replan_cannot_loop():
    lifecycle, projection = Orchestrator(max_post_judge_replans=99), _unresolved_projection()
    for i in range(20):
        decision = lifecycle.post_judge_decision(projection, mandatory_requirement_ids={"r"},
                                                 reasonable_path=True, budget_remaining=True)
        assert decision.action == (OrchestratorAction.REPLAN if i == 0 else OrchestratorAction.FINAL)
    assert lifecycle.post_judge_replans == 1


def test_upstream_inconsistency_requires_new_judge_pass_not_presentation_downgrade():
    invalid = AnswerProjection(goal_id="g", goal_status=GoalStatus.COMPLETE,
        propositions=[AnswerProposition(statement="fact", kind="FACT", coverage="SATISFIED")]).frozen_snapshot()
    valid = _unresolved_projection()
    outputs = iter([invalid, valid])
    lifecycle = Orchestrator()
    result = lifecycle.evaluate(lambda: next(outputs))
    assert result is valid
    assert invalid.goal_status == GoalStatus.COMPLETE
    assert lifecycle.judge_attempts == 2 and lifecycle.governed_retries == 1
    with pytest.raises(GovernedStateError):
        Orchestrator().evaluate(lambda: invalid)


@pytest.mark.parametrize("success", [True, False])
def test_engine_post_judge_continuation_reuses_work_and_reruns_judge(tmp_path, success):
    engine = _engine_without_live_sources(tmp_path)
    engine.interpreter = _StubInterpreter(SemanticInterpretation(raw_query="question", language="en",
        analysis_requests=[AnalysisRequest(text="question", objective="question")]))
    plan_calls, projections = [], []
    class Planner:
        available = True
        def plan(self, **kwargs):
            plan_calls.append(kwargs)
            requirement = kwargs["goal"].requirements[0]
            if len(plan_calls) == 1:
                return [PlannerNeed(id="initial", objective="initial local candidate", capability="local_analytics",
                                    requires=[requirement.id])], "initial", []
            return [PlannerNeed(id="after_judge", objective="new acquisition path", capability="web_research",
                                requires=[requirement.id])], "post-Judge", []
    engine.planner = Planner()
    def execute(ctx, semantic, needs, artifacts, outcomes, trace):
        for need in needs:
            if need.status == NeedStatus.PENDING:
                need.status = NeedStatus.SATISFIED
                artifacts.append(Artifact(id="initial" if need.id == "initial" else "acquired",
                                          kind="WEB_EVIDENCE", need_id=need.id))
    engine._execute_pending = execute
    def evaluate(goal, artifacts, needs, **kwargs):
        complete = any(a.id == "acquired" for a in artifacts) and success
        props = [p.model_copy(update={"coverage": CoverageState.SATISFIED if complete else CoverageState.UNSUPPORTED})
                 for p in goal.propositions]
        claims = [Claim(text="A hitter hit 35 home runs.", proposition_ids=[p.id for p in props],
                        support_refs=[Reference(ref_type=ReferenceType.ARTIFACT, ref_id="acquired")])] if complete else []
        result = approved_snapshot(AnswerProjection(goal_id=goal.id, goal_status=GoalStatus.COMPLETE if complete else GoalStatus.LIMITED,
            claims=claims, propositions=props, requirement_assessments=[
                RequirementAssessment(requirement_id=r.id,
                    status=RequirementStatus.SATISFIED if complete else RequirementStatus.UNSATISFIED,
                    coverage=CoverageState.SATISFIED if complete else CoverageState.UNSUPPORTED,
                    note="missing acquired relation" if not complete else "supported") for r in goal.requirements
            ]))
        projections.append((result, result.model_dump_json()))
        return result
    engine.judge.evaluate = evaluate
    result = engine.ask("post_judge", "question", trace_level="off")
    assert len(plan_calls) == 2 and len(projections) == 2
    assert "missing acquired relation" in plan_calls[1]["feedback"]
    assert result.lifecycle_usage["post_judge_replans"] == 1
    assert result.lifecycle_usage["judge_evaluations"] == 2
    assert result.projection.goal_status == (GoalStatus.COMPLETE if success else GoalStatus.LIMITED)
    assert [a.id for a in result.artifacts] == ["initial", "acquired"]
    assert plan_calls[1]["artifacts"][0] is result.artifacts[0]
    assert projections[0][0].model_dump_json() == projections[0][1]
    assert result.projection is not projections[0][0]


def test_failed_composer_json_is_one_actual_provider_call_per_attempt():
    from app.response.composer import ResponseComposer
    from app.contracts.models import Goal
    calls = []
    class Model:
        configured = True
        def complete_json(self, **kwargs):
            calls.append("json")
            raise RuntimeError("owned outage")
        def complete_text(self, **kwargs):
            calls.append("text")
            raise AssertionError("a second hidden provider attempt is forbidden")
    lifecycle = Orchestrator()
    projection = AnswerProjection(goal_id="g", goal_status="COMPLETE")
    package = _package()
    text = ResponseComposer(Model()).compose(Goal(id="g", statement="q", raw_query="q"),
        SemanticInterpretation(raw_query="q"), projection, package=package, orchestrator=lifecycle)
    assert calls == ["json", "json"]
    assert lifecycle.composer_attempts == 2 and lifecycle.conformance_attempts == 0
    assert text == render_governed_package(package)


def test_engine_presentation_regeneration_never_invokes_planner(tmp_path):
    from app.response.composer import ResponseComposer
    from tests.conformance_fixtures import complete_review_data
    engine = _engine_without_live_sources(tmp_path)
    engine.interpreter = _StubInterpreter(SemanticInterpretation(raw_query="question", language="en",
        analysis_requests=[AnalysisRequest(text="question", objective="question")]))
    plan_calls = []
    class Planner:
        available = True
        def plan(self, **kwargs):
            plan_calls.append(kwargs)
            return [], "initial", []
    engine.planner = Planner()
    def evaluate(goal, artifacts, needs, **kwargs):
        props = [p.model_copy(update={"coverage": CoverageState.SATISFIED}) for p in goal.propositions]
        return approved_snapshot(AnswerProjection(goal_id=goal.id, goal_status=GoalStatus.COMPLETE,
            propositions=props, claims=[Claim(text="A hitter hit 35 home runs.",
                                             proposition_ids=[p.id for p in props])],
            requirement_assessments=[RequirementAssessment(requirement_id=r.id,
                status=RequirementStatus.SATISFIED, coverage=CoverageState.SATISFIED) for r in goal.requirements]
        ))
    engine.judge.evaluate = evaluate
    class Model:
        configured = True
        attempts = 0
        def complete_json(self, **kwargs):
            if "response-conformance assessor" in kwargs["system"]:
                return SimpleNamespace(data=complete_review_data({"statements": [{"index": 0,
                    "epistemic": "FACTUAL", "approved_claim_backed": True, "claim_markers": ["S1"],
                    "unsupported_addition": self.attempts == 1}]}))
            self.attempts += 1
            text = "An invented medical statement. [S1]" if self.attempts == 1 else "A hitter hit 35 home runs. [S1]"
            return SimpleNamespace(data={"statements": [{"kind": "ANALYSIS", "text": text}]})
    engine.composer = ResponseComposer(Model())
    result = engine.ask("presentation_only", "question", trace_level="off")
    assert len(plan_calls) == 1
    assert result.lifecycle_usage["post_judge_replans"] == 0
    assert result.lifecycle_usage["composer_attempts"] == result.lifecycle_usage["semantic_conformance_attempts"] == 2
    assert "invented" not in result.answer
    assert result.projection.goal_status == GoalStatus.COMPLETE
