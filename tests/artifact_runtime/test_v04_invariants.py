"""v0.4 runtime invariant tests.

These tests assert architectural invariants, not individual dogfood questions:

* requested / declared / verified scope truth,
* independent Judge participation and veto authority,
* the frozen user-obligation baseline,
* explicit Artifact bindings (no ambient export injection),
* faithful Safe IR semantics (verified by numerical execution, not SQL text),
* durable ToolOutcome recovery,
* persistence identity after restore,
* claim grounding and type sufficiency,
* Shared Knowledge governance.
"""

import unittest
from datetime import date

import duckdb

from app.artifact_runtime.analytical_ir import (Aggregate, AggregateRef, AnalyticalQuery,
                                                And, BinaryOperand, Compare, InSet,
                                                PeriodSelector, Selection)
from app.artifact_runtime.artifacts import ArtifactStore
from app.artifact_runtime.bindings import resolve_bindings
from app.artifact_runtime.claims import build_claims, validate_claims
from app.artifact_runtime.engine import ArtifactRuntime
from app.artifact_runtime.ir_compiler import compile_analytical_query
from app.artifact_runtime.judge import DeterministicContextJudge, JudgeRequest
from app.artifact_runtime.obligations import (extract_obligations, obligation_coverage)
from app.artifact_runtime.planner import DeterministicPlanner, ScriptedPlanner
from app.artifact_runtime.recovery import classify, normalize_outcome
from app.artifact_runtime.response import DeterministicResponseComposer
from app.artifact_runtime.schema_catalog import catalog_from_registry
from app.artifact_runtime.scope_verification import (ScopeVerdict, verify_artifact_scope,
                                                     verified_verdict)
from app.artifact_runtime.sufficiency import CoverageJudge
from app.artifact_runtime.tool_base import RuntimeTool, ToolContext, ToolOutcome, ToolRegistry
from app.knowledge.candidates import (CandidateKnowledge, CandidateKnowledgeStore,
                                      CandidateScope)
from app.knowledge.governance import (ConflictCheckUnavailable, GovernanceError,
                                      KnowledgeGovernance)
from app.models.artifact_runtime import (ArtifactExport, CoverageAssessment, Goal, Need,
                                         RuntimeArtifact, Scope, ScopeVerification,
                                         SemanticBrief, ToolCapabilityContract)
from app.models.contracts import TimeRange
from app.models.knowledge import KnowledgeItem
from app.persistence.store import SqliteOperationalStore

from tests.artifact_runtime.fakes import (FakeWeb, RecordingExecutor, ScriptedInterpreter,
                                          dfa_item, entity_lookup, fake_roster, knowledge_base,
                                          need)
from tests.artifact_runtime.harness import build_test_runtime
from tests.artifact_runtime.test_composition import _local_ir
from tests.artifact_runtime.test_governance import _item


def _verified(artifact_id, *, dims, kind="analytics", status="OK", **changes):
    artifact = RuntimeArtifact(
        artifact_id=artifact_id, kind=kind, status=status,
        actual_scope=changes.pop("actual_scope", None),
        confidence=changes.pop("confidence", 0.9),
        metadata=changes.pop("metadata", {}), **changes)
    verifications = tuple(ScopeVerification(
        dimension=dimension, status=state, verifier="test",
        evidence_refs=(artifact_id,)) for dimension, state in dims.items())
    return artifact.model_copy(update={"scope_verifications": verifications})


# ---------------------------------------------------------------------------
# Scope truth
# ---------------------------------------------------------------------------


class ScopeTruthTests(unittest.TestCase):
    def test_tool_echoing_requested_scope_is_not_verified(self):
        requested = Scope(entities=("Yankees",), seasons=(2025,), metric="OPS")
        artifact = RuntimeArtifact(artifact_id="a", kind="unknown_tool",
                                   actual_scope=requested, status="OK",
                                   metadata={"execution_receipt": {}})
        verifications = verify_artifact_scope(requested, artifact)
        self.assertTrue(all(item.status != "VERIFIED" for item in verifications))
        verdict = verified_verdict(requested, (artifact.model_copy(
            update={"scope_verifications": verifications}),))
        self.assertNotEqual(verdict.aggregate, 1.0)

    def test_declared_scope_matching_is_only_partial_without_receipt(self):
        requested = Scope(metric="OPS")
        artifact = RuntimeArtifact(artifact_id="a", kind="unknown_tool",
                                   actual_scope=Scope(metric="OPS"), status="OK")
        verifications = verify_artifact_scope(requested, artifact)
        self.assertEqual(verifications[0].status, "PARTIAL")

    def test_unknown_verification_is_not_coverage(self):
        requested = Scope(population="players")
        artifact = RuntimeArtifact(artifact_id="a", kind="analytics", status="OK",
                                   actual_scope=Scope(population="players"))
        verifications = verify_artifact_scope(requested, artifact)
        self.assertEqual(verifications[0].status, "UNKNOWN")

    def test_broader_period_is_a_mismatch(self):
        requested = Scope(time_range=TimeRange(start=date(2025, 8, 1), end=date(2025, 8, 31)))
        artifact = RuntimeArtifact(
            artifact_id="a", kind="analytics", status="OK",
            metadata={"execution_receipt": {"applied_window": ("2025-01-01", "2025-12-31")}})
        verifications = verify_artifact_scope(requested, artifact)
        self.assertEqual(verifications[0].status, "MISMATCH")

    def test_wrong_game_type_is_a_mismatch(self):
        requested = Scope(game_types=("POSTSEASON",))
        artifact = RuntimeArtifact(
            artifact_id="a", kind="analytics", status="OK",
            metadata={"execution_receipt": {"game_types": ["REGULAR_SEASON"]}})
        verifications = verify_artifact_scope(requested, artifact)
        self.assertEqual(verifications[0].status, "MISMATCH")

    def test_wrong_population_is_a_mismatch(self):
        requested = Scope(population="pitchers")
        artifact = RuntimeArtifact(
            artifact_id="a", kind="analytics", status="OK",
            metadata={"execution_receipt": {"population": "players"}})
        verifications = verify_artifact_scope(requested, artifact)
        self.assertEqual(verifications[0].status, "MISMATCH")

    def test_historical_membership_cannot_be_certified_by_active_roster(self):
        requested = Scope(population="players", membership_basis="official_roster",
                          seasons=(2025,))
        artifact = RuntimeArtifact(
            artifact_id="roster-1", kind="team_roster", status="OK",
            actual_scope=Scope(entities=("Dodgers",), population="players",
                               membership_basis="active_roster"),
            metadata={"execution_receipt": {"team": "Dodgers", "roster_type": "active",
                                            "population": "players",
                                            "membership_basis": "active_roster"}})
        verifications = {item.dimension: item.status
                         for item in verify_artifact_scope(requested, artifact)}
        self.assertEqual(verifications["membership"], "MISMATCH")
        self.assertEqual(verifications["season"], "MISMATCH")

    def test_active_roster_request_is_verified(self):
        requested = Scope(population="players", membership_basis="active_roster")
        artifact = RuntimeArtifact(
            artifact_id="roster-1", kind="team_roster", status="OK",
            actual_scope=Scope(entities=("Dodgers",), population="players",
                               membership_basis="active_roster"),
            metadata={"execution_receipt": {"team": "Dodgers", "roster_type": "active",
                                            "population": "players"}})
        verifications = {item.dimension: item.status
                         for item in verify_artifact_scope(requested, artifact)}
        self.assertEqual(verifications["membership"], "VERIFIED")


# ---------------------------------------------------------------------------
# Judge
# ---------------------------------------------------------------------------


class _RejectingJudge:
    name = "rejecting"

    def assess(self, request):
        from app.models.artifact_runtime import JudgeAssessment
        return JudgeAssessment(assessment_id="j", goal_id=request.goal.goal_id,
                               need_id=request.need.need_id, outcome="UNSATISFIED",
                               reasons=("rejected apparently matching evidence",))


class _UnavailableJudge:
    name = "unavailable"

    def assess(self, request):
        raise RuntimeError("judge offline")


class JudgeInvariantTests(unittest.TestCase):
    def _goal(self):
        return Goal(goal_id="g", statement="x")

    def _need(self, cap="local_analytics", objective="rate"):
        return Need(need_id="n", objective=objective, proposed_capability=cap,
                    linked_artifacts=("a",))

    def test_independent_judge_can_veto_apparently_sufficient_evidence(self):
        artifact = _verified("a", dims={"entity": "VERIFIED"})
        judge = CoverageJudge(independent_judge=_RejectingJudge())
        assessment = judge.assess_need(self._goal(), self._need(), (artifact,))
        self.assertNotEqual(assessment.verdict, "SATISFIED")

    def test_judge_cannot_override_hard_deterministic_mismatch(self):
        class UpgradingJudge:
            name = "upgrading"

            def assess(self, request):
                from app.models.artifact_runtime import JudgeAssessment
                return JudgeAssessment(assessment_id="j", goal_id=request.goal.goal_id,
                                       need_id=request.need.need_id, outcome="SATISFIED")

        need = self._need()
        need = need.model_copy(update={"required_scope": Scope(seasons=(2025,))})
        artifact = _verified("a", dims={"season": "MISMATCH"})
        judge = CoverageJudge(independent_judge=UpgradingJudge())
        assessment = judge.assess_need(self._goal(), need, (artifact,))
        self.assertNotEqual(assessment.verdict, "SATISFIED")

    def test_judge_outage_is_explicit_not_success(self):
        artifact = _verified("a", dims={"entity": "VERIFIED"})
        judge = CoverageJudge(independent_judge=_UnavailableJudge())
        assessment = judge.assess_need(self._goal(), self._need(), (artifact,))
        self.assertFalse(assessment.assessment_available)
        self.assertNotEqual(assessment.verdict, "SATISFIED")

    def test_joint_support_from_multiple_artifacts(self):
        need = self._need()
        need = need.model_copy(update={
            "linked_artifacts": ("a1", "a2"),
            "required_scope": Scope(entities=("Yankees",),
                                    time_range=TimeRange(start=date(2025, 4, 1), end=date(2025, 9, 30)))})
        a1 = _verified("a1", dims={"entity": "VERIFIED", "time": "UNKNOWN"})
        a2 = _verified("a2", dims={"entity": "UNKNOWN", "time": "VERIFIED"})
        assessment = CoverageJudge().assess_need(self._goal(), need, (a1, a2))
        self.assertEqual(assessment.verdict, "SATISFIED")

    def test_measurement_alone_cannot_support_an_explanation_need(self):
        need = self._need(objective="explain why the rate changed")
        artifact = _verified("a", dims={"entity": "VERIFIED"})
        assessment = CoverageJudge().assess_need(self._goal(), need, (artifact,))
        self.assertNotEqual(assessment.verdict, "SATISFIED")

    def test_planner_weakened_decomposition_fails_the_obligation_check(self):
        goal = Goal(goal_id="g", statement="2025 Yankees OPS",
                    obligations=extract_obligations(
                        message="2025 Yankees OPS", brief=SemanticBrief(brief_id="b")))
        # A "satisfied" Need that does not reflect the explicit season/measure.
        satisfied = Need(need_id="n", objective="something else",
                         proposed_capability="shared_knowledge",
                         linked_artifacts=("k",))
        assessment = CoverageAssessment(assessment_id="x", goal_id="g", need_id="n",
                                        verdict="SATISFIED", core_goal_supported=True,
                                        supported_claims=("k",))
        artifact = RuntimeArtifact(artifact_id="k", kind="knowledge", status="OK")
        coverage = obligation_coverage(goal, (satisfied,), (assessment,), (artifact,))
        self.assertTrue(all(state != "VERIFIED" for state in coverage.values()))


# ---------------------------------------------------------------------------
# Binding isolation
# ---------------------------------------------------------------------------


class BindingIsolationTests(unittest.TestCase):
    def test_only_declared_dependency_export_binds(self):
        roster_a = need("a", "roster", produces=("PLAYER_ID_SET",),
                        parameters={"team": "New York Yankees"})
        roster_b = need("b", "roster", produces=("PLAYER_ID_SET",),
                        parameters={"team": "New York Mets"})
        sql = need("sql", "local_analytics", produces=("STATISTICAL_RESULT",),
                   parameters={"analytical_query": _local_ir()}, depends_on=("a",))
        runtime, executor = build_test_runtime(
            needs=(roster_a, roster_b, sql),
            executor=RecordingExecutor(rows=((660271, 0.5, 10, 20),)))
        result = runtime.send_message(runtime.start_conversation(), "hard hit")
        self.assertTrue(executor.statements)
        self.assertIn("660271", executor.statements[0])
        self.assertNotIn("999999", executor.statements[0])

    def test_missing_dependency_does_not_ambiently_bind(self):
        roster_a = need("a", "roster", produces=("PLAYER_ID_SET",),
                        parameters={"team": "New York Yankees"})
        sql = need("sql", "local_analytics", produces=("STATISTICAL_RESULT",),
                   parameters={"analytical_query": _local_ir()})
        runtime, executor = build_test_runtime(
            needs=(roster_a, sql), executor=RecordingExecutor(rows=((660271, 0.5, 10, 20),)))
        result = runtime.send_message(runtime.start_conversation(), "hard hit")
        self.assertEqual(executor.statements, [])
        codes = {attempt.outcome_code for attempt in result.trace.attempts}
        self.assertIn("INPUT_UNRESOLVED", codes)

    def test_stale_previous_turn_export_cannot_bind_a_new_goal(self):
        store = SqliteOperationalStore(":memory:")
        roster_a = need("a", "roster", produces=("PLAYER_ID_SET",),
                        parameters={"team": "New York Yankees"})
        runtime, executor = build_test_runtime(needs=(roster_a,), store=store)
        conversation_id = runtime.start_conversation()
        runtime.send_message(conversation_id, "Yankees roster")
        # New goal: an analytics need with no declared dependency.
        sql = need("sql2", "local_analytics", produces=("STATISTICAL_RESULT",),
                   parameters={"analytical_query": _local_ir()})
        runtime._planner = ScriptedPlanner((sql,))  # noqa: SLF001
        result = runtime.send_message(conversation_id, "now a different question")
        self.assertEqual(executor.statements, [])
        codes = {attempt.outcome_code for attempt in result.trace.attempts}
        self.assertIn("INPUT_UNRESOLVED", codes)

    def test_binding_resolution_ignores_non_dependency_exports(self):
        roster = RuntimeArtifact(
            artifact_id="r1", kind="team_roster", status="OK",
            exports=(ArtifactExport(export_id="r1:PLAYER_ID_SET",
                                    export_type="PLAYER_ID_SET", value=[1, 2]),))
        other = RuntimeArtifact(
            artifact_id="r2", kind="team_roster", status="OK",
            exports=(ArtifactExport(export_id="r2:PLAYER_ID_SET",
                                    export_type="PLAYER_ID_SET", value=[9, 9]),))
        target = Need(need_id="sql", depends_on=("a",), input_refs=(),
                      parameters={}, proposed_capability="local_analytics")
        bindings = resolve_bindings(
            target, artifacts=(roster, other), export_refs={},
            by_need={"a": ("r1",), "b": ("r2",)}, accepted_types=("PLAYER_ID_SET",))
        self.assertEqual([item.source_artifact_id for item in bindings], ["r1"])


# ---------------------------------------------------------------------------
# Safe IR semantics
# ---------------------------------------------------------------------------


def _duckdb_rows(sql: str):
    connection = duckdb.connect(":memory:")
    try:
        connection.execute(
            "CREATE TABLE statcast_pitches (batter_id INTEGER, game_date DATE, "
            "game_type TEXT, launch_speed DOUBLE, pitch_type TEXT)")
        connection.execute(
            "INSERT INTO statcast_pitches VALUES "
            "(1, DATE '2025-05-01', 'R', 95.0, 'FF'),"
            "(1, DATE '2025-05-02', 'R', 85.0, 'FF'),"
            "(1, DATE '2025-06-01', 'R', NULL, 'FF'),"
            "(2, DATE '2025-05-03', 'R', 100.0, 'SL'),"
            "(2, DATE '2025-07-01', 'R', NULL, 'SL')")
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


class IRSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.catalog = catalog_from_registry()

    def _compile(self, query):
        result = compile_analytical_query(query, self.catalog)
        self.assertTrue(result.ok, result.detail)
        return result

    def test_window_is_compiled_into_the_executed_predicate(self):
        query = AnalyticalQuery(
            query_id="q", source_kind="POSTGRES", table="statcast_pitches",
            selections=(Selection(alias="n", kind="AGGREGATE", aggregate=Aggregate(
                op="COUNT", alias="n")),),
            date_field="game_date",
            window=TimeRange(start=date(2025, 5, 1), end=date(2025, 5, 31)))
        result = self._compile(query)
        self.assertIn("game_date BETWEEN DATE '2025-05-01' AND DATE '2025-05-31'", result.sql)
        self.assertEqual(_duckdb_rows(result.sql), [(3,)])

    def test_count_with_condition_and_period_has_numerical_semantics(self):
        query = AnalyticalQuery(
            query_id="q", source_kind="POSTGRES", table="statcast_pitches",
            selections=(
                Selection(alias="all_rows", kind="AGGREGATE", aggregate=Aggregate(
                    op="COUNT", alias="all_rows")),
                Selection(alias="in_may", kind="AGGREGATE", aggregate=Aggregate(
                    op="COUNT", alias="in_may", period="may")),
                Selection(alias="hard", kind="AGGREGATE", aggregate=Aggregate(
                    op="COUNT", alias="hard",
                    condition=Compare(field="launch_speed", operator="GTE", value=95.0))),
            ),
            date_field="game_date",
            periods=(PeriodSelector(label="may",
                                    time_range=TimeRange(start=date(2025, 5, 1), end=date(2025, 5, 31))),),
            order_by="all_rows")
        result = self._compile(query)
        rows = _duckdb_rows(result.sql)
        self.assertEqual(rows[0], (5, 3, 2))

    def test_conditional_count_non_null_and_avg_semantics(self):
        query = AnalyticalQuery(
            query_id="q", source_kind="POSTGRES", table="statcast_pitches",
            selections=(
                Selection(alias="measured", kind="AGGREGATE", aggregate=Aggregate(
                    op="COUNT_NON_NULL", field="launch_speed", alias="measured")),
                Selection(alias="hard_avg", kind="AGGREGATE", aggregate=Aggregate(
                    op="AVG", field="launch_speed", alias="hard_avg",
                    condition=Compare(field="launch_speed", operator="GTE", value=95.0))),
            ),
            order_by="measured")
        result = self._compile(query)
        rows = _duckdb_rows(result.sql)
        self.assertEqual(rows[0][0], 3)
        self.assertAlmostEqual(rows[0][1], 97.5)

    def test_null_and_zero_denominator_behavior_is_explicit(self):
        query = AnalyticalQuery(
            query_id="q", source_kind="POSTGRES", table="statcast_pitches",
            selections=(
                Selection(alias="measured", kind="AGGREGATE", aggregate=Aggregate(
                    op="COUNT_NON_NULL", field="launch_speed", alias="measured")),
                Selection(alias="n", kind="AGGREGATE", aggregate=Aggregate(
                    op="COUNT", alias="n", condition=Compare(
                        field="launch_speed", operator="GT", value=1000.0))),
                Selection(alias="rate", kind="DERIVED", expression=BinaryOperand(
                    op="SAFE_DIV", left=AggregateRef(alias="n"),
                    right=AggregateRef(alias="measured"))),
            ),
            order_by="measured")
        result = self._compile(query)
        rows = _duckdb_rows(result.sql)
        self.assertEqual(rows[0][2], 0.0)

    def test_unsupported_combination_is_rejected(self):
        query = AnalyticalQuery(
            query_id="q", source_kind="POSTGRES", table="statcast_pitches",
            selections=(Selection(alias="bad", kind="AGGREGATE", aggregate=Aggregate(
                op="AVG", field="pitch_type", alias="bad")),))
        result = compile_analytical_query(query, self.catalog)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "UNSUPPORTED_OPERATION")

    def test_alias_cannot_inject_sql_structure(self):
        with self.assertRaises(Exception):
            AnalyticalQuery(
                query_id="q", source_kind="POSTGRES", table="statcast_pitches",
                selections=(Selection(alias="n, 999 AS forged", kind="AGGREGATE",
                                      aggregate=Aggregate(op="COUNT", alias="n")),))

    def test_min_rows_qualification_is_compiled(self):
        query = AnalyticalQuery(
            query_id="q", source_kind="POSTGRES", table="statcast_pitches",
            selections=(
                Selection(alias="batter", kind="GROUP_KEY", field="batter_id"),
                Selection(alias="m", kind="AGGREGATE", aggregate=Aggregate(
                    op="COUNT_NON_NULL", field="launch_speed", alias="m")),
            ),
            min_rows=2, order_by="m")
        result = self._compile(query)
        self.assertIn("HAVING COUNT(launch_speed) >= 2", result.sql)
        self.assertEqual(_duckdb_rows(result.sql), [(1, 2)])

    def test_condition_and_period_combined_has_semantics(self):
        query = AnalyticalQuery(
            query_id="q", source_kind="POSTGRES", table="statcast_pitches",
            selections=(
                Selection(alias="may_hard", kind="AGGREGATE", aggregate=Aggregate(
                    op="COUNT", alias="may_hard", period="may",
                    condition=Compare(field="launch_speed", operator="GTE", value=95.0))),
            ),
            date_field="game_date",
            periods=(PeriodSelector(label="may",
                                    time_range=TimeRange(start=date(2025, 5, 1), end=date(2025, 5, 31))),),
            order_by="may_hard")
        result = self._compile(query)
        self.assertEqual(_duckdb_rows(result.sql), [(2,)])


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------


class _ExplodingTool(RuntimeTool):
    contract = ToolCapabilityContract(name="explode", produces=("STATISTICAL_RESULT",))

    def run(self, request, context):
        raise RuntimeError("unexpected tool crash")


class _FlakyTool(RuntimeTool):
    contract = ToolCapabilityContract(name="flaky", produces=("STATISTICAL_RESULT",))
    calls = 0

    def run(self, request, context):
        type(self).calls += 1
        return ToolOutcome(recovery_code="SOURCE_QUERY_FAILED", detail="transient")


class RecoveryInvariantTests(unittest.TestCase):
    def test_unexpected_exception_is_normalized_to_a_safe_outcome(self):
        runtime, _ = build_test_runtime(needs=(need("e", "explode"),))
        runtime._registry.register(_ExplodingTool())  # noqa: SLF001
        result = runtime.send_message(runtime.start_conversation(), "boom")
        codes = {attempt.outcome_code for attempt in result.trace.attempts}
        self.assertIn("INTERNAL_FAILURE", codes)
        self.assertNotEqual(result.status, "COMPLETE")

    def test_no_artifact_failure_becomes_a_recovery_gap(self):
        runtime, _ = build_test_runtime(needs=(need("f", "flaky"),))
        runtime._registry.register(_FlakyTool())  # noqa: SLF001
        result = runtime.send_message(runtime.start_conversation(), "flaky")
        self.assertTrue(any("source_transient" in gap.lower() or "SOURCE_TRANSIENT" in gap
                            for gap in result.coverage.gaps))

    def test_failed_action_is_not_silently_retried(self):
        _FlakyTool.calls = 0
        runtime, _ = build_test_runtime(needs=(need("f", "flaky"),))
        runtime._registry.register(_FlakyTool())  # noqa: SLF001
        runtime.send_message(runtime.start_conversation(), "flaky")
        self.assertEqual(_FlakyTool.calls, 1)

    def test_recovery_is_visible_in_the_trace_projection(self):
        runtime, _ = build_test_runtime(needs=(need("f", "flaky"),))
        runtime._registry.register(_FlakyTool())  # noqa: SLF001
        result = runtime.send_message(runtime.start_conversation(), "flaky")
        self.assertTrue(result.trace.events)
        event_types = {event.event_type for event in result.trace.events}
        self.assertIn("EXECUTION_OUTCOME", event_types)
        self.assertEqual(classify("SOURCE_QUERY_FAILED"), "SOURCE_TRANSIENT")

    def test_normalize_outcome_maps_exception(self):
        outcome = normalize_outcome("t", None, RuntimeError("x"))
        self.assertEqual(classify(outcome.recovery_code), "INTERNAL_FAILURE")


# ---------------------------------------------------------------------------
# Persistence identity
# ---------------------------------------------------------------------------


class PersistenceIdentityTests(unittest.TestCase):
    def _runtime(self, store):
        runtime, _ = build_test_runtime(
            needs=(need("k", "shared_knowledge", produces=("KNOWLEDGE_CANDIDATE",)),),
            store=store)
        return runtime

    def test_ids_do_not_collide_after_restore_and_append(self):
        store = SqliteOperationalStore(":memory:")
        runtime = self._runtime(store)
        conversation_id = runtime.start_conversation()
        runtime.send_message(conversation_id, "DFA?")
        before_refs = {item.ref_id for item in runtime.get_conversation(conversation_id).refs.all()}

        revived = self._runtime(store)
        restored = revived.resume_conversation(conversation_id)
        revived.send_message(conversation_id, "and waivers?")
        after_refs = {item.ref_id for item in restored.refs.all()}
        new_ids = after_refs - before_refs
        self.assertTrue(new_ids)
        self.assertTrue(all(item not in before_refs for item in new_ids))

    def test_artifact_ids_and_lineage_survive_restart(self):
        store = SqliteOperationalStore(":memory:")
        runtime = self._runtime(store)
        conversation_id = runtime.start_conversation()
        runtime.send_message(conversation_id, "DFA?")
        artifacts = runtime.get_conversation(conversation_id).artifacts.all()
        self.assertTrue(artifacts)

        revived = self._runtime(store)
        restored = revived.resume_conversation(conversation_id)
        restored_ids = {item.artifact_id for item in restored.artifacts.all()}
        self.assertTrue(restored_ids.issuperset({item.artifact_id for item in artifacts}))
        for artifact in restored.artifacts.all():
            for parent in artifact.lineage:
                self.assertIn(parent, restored_ids)

    def test_interrupted_attempt_is_explicit(self):
        runtime, _ = build_test_runtime(needs=(need("e", "explode"),))
        runtime._registry.register(_ExplodingTool())  # noqa: SLF001
        conversation_id = runtime.start_conversation()
        runtime.send_message(conversation_id, "boom")
        conversation = runtime.get_conversation(conversation_id)
        self.assertTrue(conversation.attempts)
        self.assertTrue(any(item.status in ("FAILED", "INTERRUPTED", "UNCERTAIN")
                            for item in conversation.attempts))

    def test_persistence_failure_is_surfaced(self):
        class BrokenStore:
            def save_object(self, *args, **kwargs):
                raise OSError("disk full")

            def get_object(self, *args, **kwargs):
                return None

        runtime, _ = build_test_runtime(
            needs=(need("k", "shared_knowledge", produces=("KNOWLEDGE_CANDIDATE",)),),
            store=BrokenStore())
        conversation_id = runtime.start_conversation()
        runtime.send_message(conversation_id, "DFA?")
        conversation = runtime.get_conversation(conversation_id)
        self.assertTrue(conversation.persistence_error)
        self.assertTrue(any(event.event_type == "PERSISTENCE_ERROR"
                            for event in conversation.events.all()))


# ---------------------------------------------------------------------------
# Grounding
# ---------------------------------------------------------------------------


class GroundingInvariantTests(unittest.TestCase):
    def test_mixed_fetched_and_snippet_evidence_is_partial_and_not_quoted(self):
        web = FakeWeb([
            {"url": "https://example.test/a", "title": "Fetched", "fetched": True,
             "text": "GROUNDED PAGETEXT"},
            {"url": "https://example.test/b", "title": "Snippet", "fetched": False,
             "snippet": "UNGROUNDED SNIPPET", "text": ""},
        ])
        runtime, _ = build_test_runtime(
            needs=(need("w", "web_research", produces=("WEB_EVIDENCE",)),), web=web)
        result = runtime.send_message(runtime.start_conversation(), "news")
        artifacts = [a for a in result.artifacts if a.kind == "web_evidence"]
        self.assertTrue(artifacts)
        artifact = artifacts[0]
        self.assertEqual(artifact.status, "PARTIAL")
        self.assertIn("GROUNDED PAGETEXT", artifact.text_content)
        self.assertNotIn("UNGROUNDED SNIPPET", artifact.text_content)

    def test_derived_artifact_cannot_upgrade_rejected_evidence(self):
        from app.artifact_runtime.tools_evidence import EvidenceEntityTool

        store = ArtifactStore()
        rejected = RuntimeArtifact(artifact_id="bad-1", kind="web_evidence",
                                   status="REJECTED", text_content="Ohtani")
        store.add(rejected)
        from app.artifact_runtime.references import ReferenceStore
        refs = ReferenceStore()
        ref = refs.add("ARTIFACT", "bad-1")
        context = ToolContext(refs=refs, artifacts=store, today=date(2025, 9, 30),
                              entity_lookup=entity_lookup())
        from app.models.artifact_runtime import ToolRequest
        outcome = EvidenceEntityTool().run(
            ToolRequest(request_id="r", capability="evidence_entities",
                        input_refs=(ref.ref_id,)), context)
        self.assertEqual(outcome.recovery_code, "INPUT_INCOMPATIBLE")

    def test_unsupported_causal_statement_is_not_accepted(self):
        goal = Goal(goal_id="g", statement="why did the rate change")
        need = Need(need_id="n", objective="explain why", linked_artifacts=("a",))
        analytics = RuntimeArtifact(artifact_id="a", kind="analytics", status="OK",
                                    text_content="rate rose")
        assessment = CoverageAssessment(assessment_id="x", goal_id="g", need_id="n",
                                        verdict="SATISFIED", supported_claims=("a",))
        claims = build_claims(goal, (need,), (analytics,), (assessment,))
        self.assertTrue(claims)
        self.assertEqual(claims[0].claim_type, "REPORTED_EXPLANATION")
        accepted = validate_claims(claims, (analytics,), _EmptyRefs())
        self.assertFalse(accepted)

    def test_causal_claim_is_downgraded_without_narrative_evidence(self):
        from app.artifact_runtime.claims import validate_claims as validate
        from app.models.artifact_runtime import Claim
        analytics = RuntimeArtifact(artifact_id="a", kind="analytics", status="OK",
                                    text_content="rate rose")
        claim = Claim(claim_id="c", text="X caused the change", claim_type="CAUSAL_CLAIM",
                      support_refs=("a",))
        accepted = validate((claim,), (analytics,), _EmptyRefs())
        self.assertEqual(accepted[0].claim_type, "HYPOTHESIS")


class _EmptyRefs:
    def maybe(self, ref_id):
        return None


# ---------------------------------------------------------------------------
# Shared Knowledge governance
# ---------------------------------------------------------------------------


class KnowledgeGovernanceInvariantTests(unittest.TestCase):
    def setUp(self):
        self.candidates = CandidateKnowledgeStore(":memory:")
        self.knowledge = knowledge_base((_item(),))
        self.governance = KnowledgeGovernance(self.candidates, self.knowledge)

    def _candidate(self, **changes):
        fields = dict(candidate_id="c1", proposed_type="COMMUNITY_REFERENCE",
                      surface="太鼓达人", meaning="A community nickname",
                      language="zh", evidence=("quoted source",),
                      provenance=("https://example.test/a",),
                      scope=CandidateScope(community="community-a", language="zh",
                                           effective_from=date(2024, 1, 1),
                                           authority="COMMUNITY"))
        fields.update(changes)
        candidate = CandidateKnowledge(**fields)
        self.candidates.submit(candidate)
        return candidate

    def test_candidate_cannot_appear_in_active_retrieval(self):
        self._candidate()
        matches = self.knowledge.search("太鼓达人")
        self.assertTrue(all(match.item.knowledge_id != "CAND-c1" for match in matches))
        self.assertTrue(all(match.item.status == "ACTIVE" for match in matches))

    def test_scoped_community_meanings_can_coexist(self):
        self._candidate(scope=CandidateScope(community="community-a", authority="COMMUNITY"))
        existing = self.knowledge.store.get_item("TERM:太鼓达人")
        self.knowledge.store.upsert_item(existing.model_copy(
            update={"tags": (existing.tags + ("community-a",))}))
        check = self.governance.check_conflicts(self.candidates.get("c1"))
        self.assertTrue(check.available)
        self.assertFalse(any(record.kind == "SCOPE_CONFLICT" for record in check.records))

    def test_conflict_check_failure_does_not_auto_approve(self):
        self._candidate()

        def broken_search(*args, **kwargs):
            raise RuntimeError("retrieval down")

        self.knowledge.search = broken_search
        with self.assertRaises(ConflictCheckUnavailable):
            self.governance.approve("c1")
        self.assertEqual(self.candidates.get("c1").status, "CANDIDATE")

    def test_promotion_preserves_scope_and_provenance(self):
        self._candidate()
        item = self.governance.approve("c1", supersede=True, note="reviewed",
                                       admin="admin@example")
        payload = item.structured_payload
        self.assertEqual(payload["candidate_category"], "COMMUNITY_REFERENCE")
        self.assertEqual(payload["community"], "community-a")
        self.assertEqual(payload["reviewer"], "admin@example")
        self.assertIn("https://example.test/a", item.source_refs)
        self.assertIn("community-a", item.tags)

    def test_promotion_is_idempotent(self):
        self._candidate()
        first = self.governance.approve("c1", supersede=True)
        second = self.governance.approve("c1", supersede=True)
        self.assertEqual(first.knowledge_id, second.knowledge_id)
        self.assertEqual(first.version, second.version)


# ---------------------------------------------------------------------------
# End-to-end runtime invariants (dogfood failure classes, generalized)
# ---------------------------------------------------------------------------


class RuntimeInvariantIntegrationTests(unittest.TestCase):
    def test_historical_postseason_roster_cannot_complete(self):
        roster = need("roster", "roster", produces=("PLAYER_ID_SET",),
                      parameters={"team": "New York Yankees"},
                      scope=Scope(entities=("New York Yankees",), population="players",
                                  membership_basis="official_roster", seasons=(2025,),
                                  game_types=("POSTSEASON",)))
        sql = need("sql", "local_analytics", produces=("STATISTICAL_RESULT",),
                   parameters={"analytical_query": _local_ir()},
                   depends_on=("roster",),
                   scope=Scope(entities=("New York Yankees",), population="players",
                               membership_basis="official_roster", seasons=(2025,),
                               game_types=("POSTSEASON",)))
        runtime, _ = build_test_runtime(
            needs=(roster, sql), executor=RecordingExecutor(rows=((660271, 0.5, 10, 20),)))
        result = runtime.send_message(runtime.start_conversation(),
                                      "2025 postseason Yankees batters average EV")
        self.assertNotEqual(result.status, "COMPLETE")
        statuses = {need.need_id: need.status for need in result.trace.needs}
        self.assertNotEqual(statuses.get("roster"), "SATISFIED")
        # The mismatch must be visible, not silent.
        roster_artifact = next(a for a in result.artifacts if a.kind == "team_roster")
        self.assertTrue(roster_artifact.has_hard_mismatch())

    def test_local_analytics_no_artifact_failure_is_diagnosable(self):
        bad = _local_ir(entity_set=False)
        bad["filters"] = [{"kind": "COMPARE", "field": "ghost_field",
                           "operator": "EQ", "value": "x"}]
        sql = need("sql", "local_analytics", produces=("STATISTICAL_RESULT",),
                   parameters={"analytical_query": bad})
        runtime, executor = build_test_runtime(needs=(sql,))
        result = runtime.send_message(runtime.start_conversation(), "bad query")
        self.assertEqual(executor.statements, [])
        codes = {attempt.outcome_code for attempt in result.trace.attempts}
        self.assertIn("UNKNOWN_FIELD", codes)
        self.assertTrue(any("ghost_field" in attempt.detail
                            for attempt in result.trace.attempts))
        self.assertTrue(any(event.event_type == "EXECUTION_OUTCOME"
                            for event in result.trace.events))
        self.assertTrue(any("ghost_field" in gap or "UNKNOWN_FIELD" in gap
                            for gap in result.coverage.gaps))

    def test_obligation_is_reflected_in_terminal_state(self):
        # A query with an explicit qualification obligation: COMPLETE only if the work
        # reflects the threshold.
        sql = need("sql", "local_analytics", produces=("STATISTICAL_RESULT",),
                   parameters={"analytical_query": _local_ir(entity_set=False),
                               "min_rows": 10})
        runtime, _ = build_test_runtime(
            needs=(sql,), executor=RecordingExecutor(rows=((660271, 0.5, 10, 20),)))
        result = runtime.send_message(runtime.start_conversation(),
                                      "hard-hit rate, at least 10 batted balls")
        self.assertIn("QUALIFICATION",
                      {o.kind for o in result.trace.goal.obligations})
        states = set(result.coverage.obligation_coverage.values())
        self.assertIn("VERIFIED", states)


if __name__ == "__main__":
    unittest.main()
