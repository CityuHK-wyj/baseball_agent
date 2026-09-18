"""v0.5 planner-runtime convergence tests.

These test architectural properties of the convergence surface, not dogfood wording:

* trusted capability/schema context reaches planning (including truthful restrictions);
* Artifact/export availability is visible and exact, so an export cannot be invented;
* scheduling distinguishes ready / blocked / dependency-rejected / impossible;
* binding compatibility is more than export-type equality;
* ToolOutcome classes cause materially different (and non-repeating) planning;
* evidence extraction is Need-directed, span-grounded and ambiguity-preserving;
* typed contradictions cannot reach COMPLETE;
* qualification semantics compile to the measured denominator;
* multilingual inputs converge on equivalent typed obligations.
"""

import unittest
from datetime import date

import duckdb

from app.artifact_runtime.analytical_ir import (Aggregate, AnalyticalQuery, Compare,
                                                Qualification, Selection)
from app.artifact_runtime.bindings import (export_compatible, resolve_bindings,
                                           resolve_bindings_with_gaps)
from app.artifact_runtime.convergence import (BLOCKED_WAITING, DEPENDENCY_REJECTED,
                                              IMPOSSIBLE_CAPABILITY, READY,
                                              attempt_views, build_feedback,
                                              capability_views, export_views,
                                              planning_state, schema_views)
from app.artifact_runtime.contracts import contract_for
from app.artifact_runtime.ir_compiler import compile_analytical_query
from app.artifact_runtime.obligations import detect_conflicts, extract_obligations
from app.artifact_runtime.planner import (LLMPlanner, PlannerContext, ScriptedPlanner)
from app.artifact_runtime.recovery import (classify, failure_class,
                                           is_structurally_impossible, replan_hint,
                                           requires_replan)
from app.artifact_runtime.schema_catalog import catalog_from_registry
from app.artifact_runtime.sufficiency import CoverageJudge
from app.artifact_runtime.tool_base import (RuntimeTool, ToolCapabilityContract,
                                            ToolOutcome, ToolRegistry)
from app.llm.provider import FakeModelProvider
from app.models.artifact_runtime import (ArtifactExport, CoverageAssessment, Goal, Need,
                                         RuntimeArtifact, Scope, SemanticBrief,
                                         ToolAttempt, ToolRequest, UserConflict,
                                         UserObligation)
from app.models.contracts import TimeRange

from tests.artifact_runtime.fakes import (FakeWeb, RecordingExecutor, entity_lookup, need)
from tests.artifact_runtime.harness import build_test_runtime
from tests.artifact_runtime.test_composition import _local_ir

TODAY = date(2025, 9, 30)


# ---------------------------------------------------------------------------
# Capability / schema convergence surface
# ---------------------------------------------------------------------------


class _RestrictedTool(RuntimeTool):
    contract = ToolCapabilityContract(
        name="restricted", accepts=("SEARCH_QUERY",), produces=("WEB_EVIDENCE",),
        description="restricted provider", availability="CONFIG_REQUIRED",
        temporal_modes=("CURRENT_ONLY",), population_modes=("active_roster",),
        game_types=("REGULAR_SEASON",), entity_namespace="MLBAM",
        supported_measures=("OPS",), required_inputs=("query",), cost=3)

    def run(self, request, context):  # pragma: no cover - not executed here
        return ToolOutcome()


class CapabilitySurfaceTests(unittest.TestCase):
    def test_capability_views_expose_truthful_restrictions(self):
        views = capability_views(ToolRegistry((_RestrictedTool(),)))
        rendered = views[0].render()
        self.assertIn("CURRENT_ONLY", rendered)
        self.assertIn("CONFIG_REQUIRED", rendered)
        self.assertIn("REGULAR_SEASON", rendered)
        self.assertIn("measures=OPS", rendered)
        self.assertIn("requires=query", rendered)

    def test_schema_views_expose_role_meaning_operations_and_coverage(self):
        catalog = catalog_from_registry()
        views = schema_views(catalog)
        rendered = "\n".join(view.render() for view in views)
        self.assertIn("launch_speed(MEASURE", rendered)
        self.assertIn("exit velocity", rendered)
        self.assertIn("grain=pitch", rendered)
        self.assertIn("coverage=", rendered)

    def test_semantic_handle_resolves_to_physical_catalog_field(self):
        catalog = catalog_from_registry()
        query = AnalyticalQuery(
            query_id="q", source_kind="POSTGRES", table="statcast_pitches",
            selections=(
                Selection(alias="batter", kind="GROUP_KEY", field="batter"),
                Selection(alias="ev", kind="AGGREGATE", aggregate=Aggregate(
                    op="AVG", field="exit_velocity", alias="ev")),
            ),
            order_by="ev")
        result = compile_analytical_query(query, catalog)
        self.assertTrue(result.ok, result.detail)
        self.assertIn("AVG(launch_speed)", result.sql)
        self.assertIn("batter_id AS batter", result.sql)

    def test_unknown_semantic_handle_is_still_rejected(self):
        catalog = catalog_from_registry()
        query = AnalyticalQuery(
            query_id="q", source_kind="POSTGRES", table="statcast_pitches",
            selections=(Selection(alias="x", kind="AGGREGATE", aggregate=Aggregate(
                op="AVG", field="invented_velocity", alias="x")),))
        result = compile_analytical_query(query, catalog)
        self.assertEqual(result.code, "UNKNOWN_FIELD")

    def test_unsupported_table_cannot_be_compiled(self):
        catalog = catalog_from_registry()
        query = AnalyticalQuery(
            query_id="q", source_kind="POSTGRES", table="ghost_table",
            selections=(Selection(alias="n", kind="AGGREGATE", aggregate=Aggregate(
                op="COUNT", alias="n")),))
        result = compile_analytical_query(query, catalog)
        self.assertEqual(result.code, "UNKNOWN_TABLE")


class PlannerContextPromptTests(unittest.TestCase):
    def _context(self):
        artifact = RuntimeArtifact(
            artifact_id="r-1", kind="team_roster", status="OK",
            actual_scope=Scope(entities=("New York Yankees",), population="players",
                               membership_basis="active_roster"),
            exports=(ArtifactExport(
                export_id="r-1:PLAYER_ID_SET", export_type="PLAYER_ID_SET", value=[1, 2],
                contract=contract_for("PLAYER_ID_SET", scope=Scope(
                    entities=("New York Yankees",), population="players"))),))
        goal = Goal(goal_id="g", statement="Yankees hard-hit rate",
                    obligations=(UserObligation(
                        obligation_id="o1", kind="METRIC", description="measure EV"),))
        attempt = ToolAttempt(attempt_id="a1", need_id="n0", capability="local_analytics",
                              status="FAILED", outcome_code="UNKNOWN_FIELD",
                              detail="unknown field 'ghost'")
        feedback = build_feedback(goal=goal, needs=(), artifacts=(artifact,),
                                  attempts=(attempt,), obligation_coverage={"o1": "MISSING"},
                                  gaps=("o1 missing",), budget_remaining=4)
        return PlannerContext(
            capabilities=capability_views(ToolRegistry((_RestrictedTool(),))),
            schema_tables=schema_views(catalog_from_registry()),
            available_exports=export_views((artifact,), {"n0": ("r-1",)}),
            feedback=feedback, budget_remaining=4)

    def test_initial_prompt_shows_exports_and_not_invented_ones(self):
        provider = FakeModelProvider(responses=['{"needs": []}'])
        planner = LLMPlanner(provider, model="m")
        planner.initial_needs(goal=Goal(goal_id="g", statement="x"),
                              brief=SemanticBrief(brief_id="b"), context=self._context())
        prompt = provider.calls[0][1]
        self.assertIn("r-1:PLAYER_ID_SET", prompt)
        self.assertIn("New York Yankees", prompt)
        self.assertIn("UNKNOWN_FIELD", prompt)  # operational feedback is present

    def test_replan_prompt_carries_material_change_information(self):
        provider = FakeModelProvider(responses=['{"needs": []}'])
        planner = LLMPlanner(provider, model="m")
        planner.add_needs(goal=Goal(goal_id="g", statement="x"),
                          brief=SemanticBrief(brief_id="b"), existing=(),
                          artifacts=(), gaps=("coverage gap",), context=self._context())
        prompt = provider.calls[0][1]
        self.assertIn("ghost", prompt)
        self.assertIn("o1", prompt)
        self.assertIn("next:", prompt)

    def test_prompt_rendering_is_bounded(self):
        provider = FakeModelProvider(responses=['{"needs": []}'])
        planner = LLMPlanner(provider, model="m")
        planner.initial_needs(goal=Goal(goal_id="g", statement="x"),
                              brief=SemanticBrief(brief_id="b"), context=self._context())
        prompt = provider.calls[0][1]
        self.assertLess(len(prompt), 30000)


# ---------------------------------------------------------------------------
# Scheduling / dependency state
# ---------------------------------------------------------------------------


class PlanningStateTests(unittest.TestCase):
    def test_dependency_states_are_distinct(self):
        waiting = Need(need_id="w", proposed_capability="local_analytics",
                       depends_on=("missing",))
        self.assertEqual(planning_state(waiting, (waiting,), attempted=set(),
                                        unavailable=set()), BLOCKED_WAITING)
        rejected_parent = Need(need_id="p", proposed_capability="roster", status="FAILED")
        child = Need(need_id="c", proposed_capability="local_analytics",
                     depends_on=("p",))
        self.assertEqual(planning_state(child, (rejected_parent, child), attempted=set(),
                                        unavailable=set()), DEPENDENCY_REJECTED)
        impossible = Need(need_id="i", proposed_capability="roster")
        self.assertEqual(planning_state(impossible, (impossible,), attempted=set(),
                                        unavailable={"roster"}), IMPOSSIBLE_CAPABILITY)
        ready = Need(need_id="r", proposed_capability="roster")
        self.assertEqual(planning_state(ready, (ready,), attempted=set(),
                                        unavailable=set()), READY)

    def test_failed_dependency_blocks_downstream_execution(self):
        roster = need("r", "roster", produces=("PLAYER_ID_SET",),
                      parameters={"team": "Unknown Team"})
        sql = need("s", "local_analytics", produces=("STATISTICAL_RESULT",),
                   parameters={"analytical_query": _local_ir()}, depends_on=("r",))
        runtime, executor = build_test_runtime(
            needs=(roster, sql), executor=RecordingExecutor(rows=((660271, 0.5, 1, 2),)))
        result = runtime.send_message(runtime.start_conversation(), "roster dependent")
        self.assertEqual(executor.statements, [])
        codes = {attempt.outcome_code for attempt in result.trace.attempts}
        self.assertIn("SOURCE_TRANSIENT", codes)
        self.assertNotEqual(result.status, "COMPLETE")

    def test_structurally_impossible_capability_is_not_repeated(self):
        class BlockedTool(RuntimeTool):
            contract = ToolCapabilityContract(name="blocked",
                                              produces=("STATISTICAL_RESULT",))
            calls = 0

            def run(self, request, context):
                type(self).calls += 1
                return ToolOutcome(recovery_code="POLICY_BLOCKED", detail="not permitted")

        needs = (need("n1", "blocked", produces=("STATISTICAL_RESULT",)),
                 need("n2", "blocked", produces=("STATISTICAL_RESULT",)))
        runtime, _ = build_test_runtime(needs=needs)
        runtime._registry.register(BlockedTool())  # noqa: SLF001
        runtime.send_message(runtime.start_conversation(), "blocked twice")
        self.assertEqual(BlockedTool.calls, 1)


# ---------------------------------------------------------------------------
# Binding compatibility
# ---------------------------------------------------------------------------


class BindingCompatibilityTests(unittest.TestCase):
    def _artifact(self, artifact_id, team, ids):
        scope = Scope(entities=(team,), population="players",
                      membership_basis="active_roster")
        return RuntimeArtifact(
            artifact_id=artifact_id, kind="team_roster", status="OK", actual_scope=scope,
            exports=(ArtifactExport(
                export_id=f"{artifact_id}:PLAYER_ID_SET", export_type="PLAYER_ID_SET",
                value=ids, contract=contract_for("PLAYER_ID_SET", scope=scope)),))

    def test_wrong_team_export_is_incompatible(self):
        yankees = self._artifact("ny", "New York Yankees", [1])
        mets = self._artifact("nym", "New York Mets", [9])
        need_obj = Need(need_id="sql", proposed_capability="local_analytics",
                        depends_on=("roster",),
                        required_scope=Scope(entities=("New York Mets",),
                                             population="players"))
        bindings, gaps = resolve_bindings_with_gaps(
            need_obj, artifacts=(yankees, mets), export_refs={},
            by_need={"roster": ("ny",)}, accepted_types=("PLAYER_ID_SET",))
        self.assertEqual(bindings, ())
        self.assertTrue(any("entity namespace mismatch" in gap for gap in gaps))

    def test_correct_team_export_binds(self):
        yankees = self._artifact("ny", "New York Yankees", [1])
        need_obj = Need(need_id="sql", proposed_capability="local_analytics",
                        depends_on=("roster",),
                        required_scope=Scope(entities=("New York Yankees",),
                                             population="players"))
        bindings, gaps = resolve_bindings_with_gaps(
            need_obj, artifacts=(yankees,), export_refs={},
            by_need={"roster": ("ny",)}, accepted_types=("PLAYER_ID_SET",))
        self.assertEqual([item.source_export_id for item in bindings],
                         ["ny:PLAYER_ID_SET"])
        self.assertEqual(gaps, ())

    def test_two_same_type_exports_require_explicit_correct_binding(self):
        yankees = self._artifact("ny", "New York Yankees", [660271])
        mets = self._artifact("nym", "New York Mets", [999999])
        sql = need("sql", "local_analytics", produces=("STATISTICAL_RESULT",),
                   parameters={"analytical_query": _local_ir()}, depends_on=("ny",))
        roster_y = need("ny", "roster", produces=("PLAYER_ID_SET",),
                        parameters={"team": "New York Yankees"})
        roster_m = need("nym", "roster", produces=("PLAYER_ID_SET",),
                        parameters={"team": "New York Mets"})
        runtime, executor = build_test_runtime(
            needs=(roster_y, roster_m, sql),
            executor=RecordingExecutor(rows=((660271, 0.5, 1, 2),)))
        runtime.send_message(runtime.start_conversation(), "hard hit")
        self.assertTrue(executor.statements)
        self.assertIn("660271", executor.statements[0])
        self.assertNotIn("999999", executor.statements[0])

    def test_incompatible_binding_is_rejected_and_recorded(self):
        # Analytics need requires the Mets population but only declares the Yankees.
        roster_y = need("ny", "roster", produces=("PLAYER_ID_SET",),
                        parameters={"team": "New York Yankees"})
        sql = need("sql", "local_analytics", produces=("STATISTICAL_RESULT",),
                   parameters={"analytical_query": _local_ir()}, depends_on=("ny",),
                   scope=Scope(entities=("New York Mets",), population="players"))
        runtime, executor = build_test_runtime(
            needs=(roster_y, sql), executor=RecordingExecutor(rows=((660271, 0.5, 1, 2),)))
        result = runtime.send_message(runtime.start_conversation(), "mets via yankees")
        self.assertEqual(executor.statements, [])
        self.assertTrue(any(event.event_type == "BINDING_REJECTED"
                            for event in result.trace.events))

    def test_type_match_alone_is_insufficient(self):
        artifact = self._artifact("ny", "New York Yankees", [1])
        export = artifact.exports[0]
        need_obj = Need(need_id="n", required_scope=Scope(entities=("Boston Red Sox",)))
        ok, reasons = export_compatible(need_obj, export, artifact)
        self.assertFalse(ok)
        self.assertTrue(reasons)

    def test_unresolvable_entity_set_ref_recovers_from_bound_export(self):
        roster = need("r", "roster", produces=("PLAYER_ID_SET",),
                      parameters={"team": "New York Yankees"})
        ir = _local_ir()
        ir["entity_set"]["export_ref"] = "r"  # a need id, not an export id
        sql = need("sql", "local_analytics", produces=("STATISTICAL_RESULT",),
                   parameters={"analytical_query": ir}, depends_on=("r",))
        runtime, executor = build_test_runtime(
            needs=(roster, sql), executor=RecordingExecutor(rows=((660271, 0.5, 1, 2),)))
        result = runtime.send_message(runtime.start_conversation(), "hard hit")
        self.assertTrue(executor.statements, result.trace.steps)
        self.assertIn("660271", executor.statements[0])


# ---------------------------------------------------------------------------
# Recovery feedback
# ---------------------------------------------------------------------------


class RecoveryFeedbackTests(unittest.TestCase):
    def test_failure_classes_are_distinct(self):
        self.assertEqual(failure_class("UNSUPPORTED_CAPABILITY"), "UNSUPPORTED_CAPABILITY")
        self.assertEqual(failure_class("UNSUPPORTED_OPERATION"), "UNSUPPORTED_ANALYSIS")
        self.assertEqual(failure_class("UNKNOWN_FIELD"), "UNKNOWN_SCHEMA")
        self.assertEqual(failure_class("INPUT_UNRESOLVED"), "WRONG_BINDING")
        self.assertEqual(failure_class("SOURCE_TRANSIENT"), "RETRYABLE_SOURCE")
        self.assertEqual(failure_class("EMPTY_RESULT"), "VALID_EMPTY")
        self.assertEqual(failure_class("COVERAGE_UNAVAILABLE"), "INSUFFICIENT_EVIDENCE")
        self.assertEqual(failure_class("IDENTITY_AMBIGUOUS"), "IDENTITY_AMBIGUITY")

    def test_structural_impossibility_and_replan_gating(self):
        self.assertTrue(is_structurally_impossible("POLICY_BLOCKED"))
        self.assertTrue(is_structurally_impossible("UNSUPPORTED_CAPABILITY"))
        self.assertFalse(is_structurally_impossible("UNSUPPORTED_OPERATION"))
        self.assertTrue(requires_replan("UNKNOWN_FIELD"))
        self.assertFalse(requires_replan("SOURCE_TRANSIENT"))
        self.assertFalse(requires_replan("EMPTY_RESULT"))

    def test_replan_hints_differ_by_class(self):
        def hint(code):
            return replan_hint(ToolAttempt(attempt_id="a", capability="c",
                                           outcome_code=code))
        self.assertIn("different accepted export",
                      hint("INPUT_UNRESOLVED").lower())
        self.assertIn("catalog", hint("UNKNOWN_FIELD").lower())
        self.assertIn("do not retry", hint("UNSUPPORTED_CAPABILITY").lower())
        self.assertIn("legitimately returned no rows",
                      hint("EMPTY_RESULT").lower())

    def test_unsupported_operation_does_not_blacklist_capability(self):
        attempt = ToolAttempt(attempt_id="a", need_id="n", capability="local_analytics",
                              status="FAILED", outcome_code="UNSUPPORTED_OPERATION")
        feedback = build_feedback(goal=Goal(goal_id="g"), needs=(), artifacts=(),
                                  attempts=(attempt,))
        self.assertNotIn("local_analytics", feedback.unavailable_capabilities)

    def test_policy_block_blacklists_capability(self):
        attempt = ToolAttempt(attempt_id="a", need_id="n", capability="web_research",
                              status="FAILED", outcome_code="POLICY_BLOCKED")
        feedback = build_feedback(goal=Goal(goal_id="g"), needs=(), artifacts=(),
                                  attempts=(attempt,))
        self.assertIn("web_research", feedback.unavailable_capabilities)

    def test_valid_empty_is_not_execution_failure(self):
        self.assertEqual(classify("WEB_NO_RESULTS"), "EMPTY_RESULT")
        self.assertEqual(classify("SOURCE_QUERY_FAILED"), "SOURCE_TRANSIENT")

    def test_capability_absence_differs_from_data_absence(self):
        absent = need("a", "no_such_capability", produces=("STATISTICAL_RESULT",))
        runtime, _ = build_test_runtime(needs=(absent,))
        result = runtime.send_message(runtime.start_conversation(), "ghost capability")
        codes = {attempt.outcome_code for attempt in result.trace.attempts}
        self.assertIn("UNSUPPORTED_CAPABILITY", codes)
        self.assertNotIn("EMPTY_RESULT", codes)
        self.assertNotEqual(result.status, "COMPLETE")

    def test_attempt_views_carry_class_and_hint(self):
        attempt = ToolAttempt(attempt_id="a", need_id="n", capability="local_analytics",
                              status="FAILED", outcome_code="UNKNOWN_FIELD",
                              detail="unknown field ghost")
        view = attempt_views((attempt,))[0]
        self.assertEqual(view.failure_class, "UNKNOWN_SCHEMA")
        self.assertIn("ghost", view.render())


# ---------------------------------------------------------------------------
# Need-directed evidence extraction
# ---------------------------------------------------------------------------


def _multi_entity_lookup():
    from app.models.entities import CanonicalEntity
    from app.semantic.entity_resolver import EntityDictionary, EntityResolver
    from app.tools.entity_lookup import EntityLookup

    dictionary = EntityDictionary((
        CanonicalEntity(entity_key="MLBAM:1", entity_type="PLAYER",
                        display_name="Shohei Ohtani", aliases=("Ohtani",)),
        CanonicalEntity(entity_key="MLBAM:2", entity_type="PLAYER",
                        display_name="Mike Trout", aliases=("Trout",)),
        CanonicalEntity(entity_key="MLBAM:3", entity_type="PLAYER",
                        display_name="Chris Smith"),
        CanonicalEntity(entity_key="MLBAM:4", entity_type="PLAYER",
                        display_name="Chris Smith"),
    ))
    return EntityLookup(dictionary, EntityResolver(dictionary))


class NeedDirectedEvidenceTests(unittest.TestCase):
    def _context(self, text: str):
        from app.artifact_runtime.artifacts import ArtifactStore
        from app.artifact_runtime.references import ReferenceStore
        store = ArtifactStore()
        refs = ReferenceStore()
        web = RuntimeArtifact(artifact_id="web-1", kind="web_evidence", status="OK",
                              text_content=text, references=("ref-web-1",))
        store.add(web)
        ref = refs.add("ARTIFACT", "web-1")
        from app.artifact_runtime.tool_base import ToolContext
        return ToolContext(refs=refs, artifacts=store, today=TODAY,
                           entity_lookup=_multi_entity_lookup()), ref

    def test_focus_extraction_excludes_unrelated_entities(self):
        from app.artifact_runtime.tools_evidence import EvidenceEntityTool
        context, ref = self._context(
            "Shohei Ohtani homered. Mike Trout walked. Chris Smith pitched.")
        outcome = EvidenceEntityTool().run(ToolRequest(
            request_id="r", capability="evidence_entities", input_refs=(ref.ref_id,),
            structured_inputs={"focus": ["Ohtani"]}), context)
        artifact = outcome.artifacts[0]
        self.assertEqual(set(artifact.structured_data["mapping"]), {"Shohei Ohtani"})
        self.assertNotIn("Mike Trout", artifact.structured_data["mapping"])

    def test_focus_extraction_retains_source_spans(self):
        from app.artifact_runtime.tools_evidence import EvidenceEntityTool
        context, ref = self._context("Shohei Ohtani homered.")
        outcome = EvidenceEntityTool().run(ToolRequest(
            request_id="r", capability="evidence_entities", input_refs=(ref.ref_id,),
            structured_inputs={"focus": ["Ohtani"]}), context)
        spans = outcome.artifacts[0].metadata["execution_receipt"]["source_spans"]
        self.assertTrue(spans)
        self.assertEqual(spans[0]["artifact_id"], "web-1")
        self.assertIn("Shohei Ohtani", spans[0]["context"])

    def test_ambiguous_entity_is_preserved_not_guessed(self):
        from app.artifact_runtime.tools_evidence import EntityResolutionTool
        context, _ = self._context("")
        outcome = EntityResolutionTool().run(ToolRequest(
            request_id="r", capability="entity_resolution",
            structured_inputs={"mentions": ["Chris Smith"]}), context)
        artifact = outcome.artifacts[0]
        self.assertEqual(outcome.recovery_code, "IDENTITY_AMBIGUOUS")
        self.assertGreaterEqual(len(artifact.structured_data["ambiguous"]["Chris Smith"]), 2)
        self.assertNotIn("Chris Smith", artifact.structured_data["mapping"])

    def test_broad_scan_does_not_collapse_same_name_identities(self):
        from app.artifact_runtime.tools_evidence import EvidenceEntityTool
        context, ref = self._context("Chris Smith pitched.")
        outcome = EvidenceEntityTool().run(ToolRequest(
            request_id="r", capability="evidence_entities", input_refs=(ref.ref_id,)),
            context)
        artifact = outcome.artifacts[0]
        self.assertIn("Chris Smith", artifact.structured_data["ambiguous"])
        self.assertNotIn("Chris Smith", artifact.structured_data["mapping"])

    def test_unrelated_web_entities_do_not_contaminate_population(self):
        # End-to-end: focus on Ohtani only, then the downstream SQL id set contains 1.
        from app.models.entities import CanonicalEntity
        from app.semantic.entity_resolver import EntityDictionary, EntityResolver
        from app.tools.entity_lookup import EntityLookup

        dictionary = EntityDictionary((
            CanonicalEntity(entity_key="MLBAM:660271", entity_type="PLAYER",
                            display_name="Shohei Ohtani", aliases=("Ohtani",)),
            CanonicalEntity(entity_key="MLBAM:111111", entity_type="PLAYER",
                            display_name="Mike Trout", aliases=("Trout",)),
        ))
        lookup = EntityLookup(dictionary, EntityResolver(dictionary))
        web = FakeWeb([{"url": "https://example.test/a", "title": "Report",
                        "fetched": True, "text": "Ohtani and Trout both played."}])
        needs = (
            need("web", "web_research", produces=("WEB_EVIDENCE",)),
            need("entities", "evidence_entities", produces=("PLAYER_ID_SET",),
                 parameters={"focus": ["Ohtani"]}, depends_on=("web",)),
            need("sql", "local_analytics", produces=("STATISTICAL_RESULT",),
                 parameters={"analytical_query": _local_ir()}, depends_on=("entities",)),
        )
        runtime, executor = build_test_runtime(
            needs=needs, web=web,
            executor=RecordingExecutor(rows=((660271, 0.6, 3, 5),)))
        runtime._entity_lookup = lookup  # noqa: SLF001
        runtime.send_message(runtime.start_conversation(), "Ohtani focus")
        self.assertTrue(executor.statements)
        self.assertIn("660271", executor.statements[0])
        self.assertNotIn("111111", executor.statements[0])


# ---------------------------------------------------------------------------
# DB <-> Web value flow
# ---------------------------------------------------------------------------


class ValueFlowTests(unittest.TestCase):
    def test_db_values_parameterize_a_later_web_query(self):
        sql = need("sql", "local_analytics", produces=("STATISTICAL_RESULT",),
                   parameters={"analytical_query": _local_ir(entity_set=False)})
        web_need = need("web", "web_research", produces=("WEB_EVIDENCE",),
                        depends_on=("sql",))
        web = FakeWeb([{"url": "https://example.test/a", "title": "Story",
                        "fetched": True, "text": "Reported change."}])
        runtime, _ = build_test_runtime(
            needs=(sql, web_need), web=web,
            executor=RecordingExecutor(rows=((660271, 0.62, 40, 65),)))
        runtime.send_message(runtime.start_conversation(), "explain the rate")
        self.assertTrue(web.queries)
        self.assertIn("0.62", web.queries[0])

    def test_different_db_values_produce_different_web_queries(self):
        def run_with(value):
            sql = need("sql", "local_analytics", produces=("STATISTICAL_RESULT",),
                       parameters={"analytical_query": _local_ir(entity_set=False)})
            web_need = need("web", "web_research", produces=("WEB_EVIDENCE",),
                            depends_on=("sql",))
            web = FakeWeb([{"url": "https://example.test/a", "title": "Story",
                            "fetched": True, "text": "Reported change."}])
            runtime, _ = build_test_runtime(
                needs=(sql, web_need), web=web,
                executor=RecordingExecutor(rows=((660271, value, 40, 65),)))
            runtime.send_message(runtime.start_conversation(), "explain")
            return web.queries[0]

        self.assertNotEqual(run_with(0.62), run_with(0.28))


# ---------------------------------------------------------------------------
# Contradiction / impossibility
# ---------------------------------------------------------------------------


class ContradictionTests(unittest.TestCase):
    def test_future_result_is_typed_as_unknown_not_capability(self):
        conflicts = detect_conflicts(
            scope=Scope(time_range=TimeRange(start=date(2026, 9, 1), end=date(2026, 9, 30))),
            constraints=(), obligations=(), today=TODAY)
        self.assertEqual([item.kind for item in conflicts], ["FUTURE_RESULT"])
        self.assertEqual(conflicts[0].severity, "UNKNOWN")

    def test_contradictory_thresholds_are_impossible(self):
        obligations = (
            UserObligation(obligation_id="o1", kind="QUALIFICATION",
                           description="at least 50", value=">=50"),
            UserObligation(obligation_id="o2", kind="QUALIFICATION",
                           description="at most 10", value="<=10"))
        conflicts = detect_conflicts(scope=None, constraints=(), obligations=obligations,
                                     today=TODAY)
        self.assertEqual([item.kind for item in conflicts], ["CONTRADICTORY_THRESHOLD"])
        self.assertEqual(conflicts[0].severity, "IMPOSSIBLE")
        self.assertEqual(set(conflicts[0].obligation_ids), {"o1", "o2"})

    def test_impossible_window_from_constraint_text(self):
        conflicts = detect_conflicts(
            scope=None, constraints=("time scope 2025-09-01..2025-05-01",),
            obligations=(), today=TODAY)
        self.assertEqual([item.kind for item in conflicts], ["IMPOSSIBLE_TIME_RANGE"])

    def test_conflict_prevents_complete_even_with_satisfied_needs(self):
        goal = Goal(goal_id="g", statement="future",
                    conflicts=(UserConflict(conflict_id="c", kind="FUTURE_RESULT",
                                            severity="UNKNOWN",
                                            description="season has not happened"),))
        satisfied = Need(need_id="n", criticality="CORE")
        assessment = CoverageAssessment(assessment_id="a", goal_id="g", need_id="n",
                                        verdict="SATISFIED", core_goal_supported=True)
        coverage = CoverageJudge().summarize(goal, (satisfied,), (assessment,))
        self.assertFalse(coverage.core_goal_supported)
        self.assertTrue(any("not satisfiable" in gap for gap in coverage.gaps))

    def test_contradictory_request_cannot_become_complete_end_to_end(self):
        # A satisfied knowledge need plus a contradictory user requirement must remain
        # LIMITED/FAILED, never COMPLETE.
        from app.artifact_runtime.planner import RuleBasedSemanticInterpreter
        from app.models.artifact_runtime import SemanticBrief as Brief
        interpreter = _FutureInterpreter()
        runtime, _ = build_test_runtime(
            needs=(need("k", "shared_knowledge", produces=("KNOWLEDGE_CANDIDATE",)),),
            interpreter=interpreter)
        result = runtime.send_message(runtime.start_conversation(),
                                      "2027 Yankees OPS at least 50 and at most 10")
        self.assertNotEqual(result.status, "COMPLETE")
        self.assertTrue(result.trace.goal.conflicts)


class _FutureInterpreter:
    def brief(self, *, message, history, resolved_entities, unknowns, today):
        return SemanticBrief(brief_id="b", goal_statement=message, source="test")


# ---------------------------------------------------------------------------
# Qualification semantics
# ---------------------------------------------------------------------------


def _qualification_query(qualification):
    return AnalyticalQuery(
        query_id="q", source_kind="POSTGRES", table="statcast_pitches",
        selections=(
            Selection(alias="batter", kind="GROUP_KEY", field="batter_id"),
            Selection(alias="ev", kind="AGGREGATE", aggregate=Aggregate(
                op="AVG", field="launch_speed", alias="ev"))),
        qualification=qualification, order_by="ev")


class QualificationTests(unittest.TestCase):
    def setUp(self):
        self.catalog = catalog_from_registry()

    def _compile(self, qualification):
        result = compile_analytical_query(_qualification_query(qualification),
                                          self.catalog)
        self.assertTrue(result.ok, result.detail)
        return result

    def test_games_qualification_uses_distinct_games(self):
        result = self._compile(Qualification(basis="GAMES", minimum=10))
        self.assertIn("HAVING COUNT(DISTINCT game_pk) >= 10", result.sql)

    def test_measured_qualification_uses_the_measured_denominator(self):
        result = self._compile(Qualification(basis="MEASURED", minimum=30,
                                             field="launch_speed"))
        self.assertIn("HAVING COUNT(launch_speed) >= 30", result.sql)

    def test_rows_and_events_qualification_count_rows(self):
        self.assertIn("HAVING COUNT(*) >= 5",
                      self._compile(Qualification(basis="ROWS", minimum=5)).sql)
        self.assertIn("HAVING COUNT(*) >= 5",
                      self._compile(Qualification(basis="EVENTS", minimum=5)).sql)

    def test_unsupported_qualification_field_is_rejected(self):
        result = compile_analytical_query(
            _qualification_query(Qualification(basis="MEASURED", minimum=5,
                                               field="pitch_type")), self.catalog)
        self.assertEqual(result.code, "UNSUPPORTED_OPERATION")

    def test_games_qualification_has_numerical_semantics(self):
        result = self._compile(Qualification(basis="GAMES", minimum=2))
        connection = duckdb.connect(":memory:")
        try:
            connection.execute(
                "CREATE TABLE statcast_pitches (batter_id INTEGER, game_pk INTEGER, "
                "launch_speed DOUBLE)")
            connection.execute(
                "INSERT INTO statcast_pitches VALUES "
                "(1, 10, 95.0), (1, 10, 85.0), (1, 11, 90.0), (2, 12, 91.0)")
            rows = connection.execute(result.sql).fetchall()
        finally:
            connection.close()
        self.assertEqual(rows, [(1, 90.0)])


# ---------------------------------------------------------------------------
# Multilingual / metamorphic convergence
# ---------------------------------------------------------------------------


class EndToEndReplanningTests(unittest.TestCase):
    def test_llm_planner_sees_failure_feedback_and_converges(self):
        import json
        bad_ir = _local_ir(entity_set=False)
        bad_ir["filters"] = [{"kind": "COMPARE", "field": "ghost_field",
                              "operator": "EQ", "value": "x"}]

        def plan(**changes):
            need = {"need_id": "analytics", "objective": "hard-hit rate",
                    "expected_information": "rate", "criticality": "CORE",
                    "proposed_capability": "local_analytics",
                    "preferred_capabilities": ["STATISTICAL_RESULT"],
                    "parameters": {"analytical_query": _local_ir(entity_set=False)}}
            need.update(changes)
            return json.dumps({"needs": [need]})

        provider = FakeModelProvider(responses=[
            plan(parameters={"analytical_query": bad_ir}),
            plan(need_id="analytics-good")])
        runtime, executor = build_test_runtime(
            needs=(), executor=RecordingExecutor(rows=((660271, 0.62, 40, 65),)))
        runtime._planner = LLMPlanner(provider, model="m")  # noqa: SLF001
        result = runtime.send_message(runtime.start_conversation(), "hard hit rate")
        self.assertGreaterEqual(len(provider.calls), 2)
        replan_prompt = provider.calls[1][1]
        self.assertIn("UNKNOWN_SCHEMA", replan_prompt)
        self.assertIn("ghost_field", replan_prompt)
        self.assertTrue(executor.statements, result.trace.steps)
        self.assertNotIn("ghost_field", executor.statements[0])
        self.assertEqual(result.status, "COMPLETE")

    def test_alternate_route_satisfies_the_original_obligation(self):
        class ReplanningPlanner(ScriptedPlanner):
            def __init__(self):
                super().__init__((need("primary", "no_such_capability",
                                       produces=("STATISTICAL_RESULT",)),))
                self.added = 0

            def add_needs(self, **kwargs):
                if kwargs.get("gaps") and self.added == 0:
                    self.added += 1
                    return (Need(need_id="alternate", objective="hard-hit rate",
                                 proposed_capability="local_analytics",
                                 preferred_capabilities=("STATISTICAL_RESULT",),
                                 parameters={"analytical_query": _local_ir(
                                     entity_set=False)},
                                 route_of="primary",
                                 equivalence_note="same measure, different route"),)
                return ()

        runtime, executor = build_test_runtime(
            needs=(), executor=RecordingExecutor(rows=((660271, 0.62, 40, 65),)))
        runtime._planner = ReplanningPlanner()  # noqa: SLF001
        result = runtime.send_message(runtime.start_conversation(), "hard hit rate")
        self.assertTrue(executor.statements)
        self.assertEqual(result.status, "COMPLETE")
        needs = {item.need_id: item for item in result.trace.needs}
        self.assertEqual(needs["alternate"].route_of, "primary")

    def test_failed_unresolved_core_need_is_not_credited_without_evidence(self):
        """A structurally closed CORE Need with no accepted work must stay unresolved.

        Regression for the v0.5 defect where closed Needs were excluded from ``missing``
        unconditionally, yielding ``core_goal_supported=True`` and suppressing recovery.
        """
        goal = Goal(goal_id="g", statement="anything")
        core = Need(need_id="core", objective="fail", criticality="CORE",
                    proposed_capability="local_analytics")
        assessment = CoverageAssessment(assessment_id="a", goal_id="g", need_id="core",
                                        verdict="UNSATISFIED")
        coverage = CoverageJudge().summarize(goal, (core,), (assessment,),
                                             closed_needs=("core",))
        self.assertFalse(coverage.core_goal_supported)
        self.assertIn("core", coverage.missing_needs)

    def test_failed_core_need_is_asked_to_recover_when_no_obligations_exist(self):
        """The planner must be consulted so a corrected plan can be proposed."""
        class RecordingReplanPlanner(ScriptedPlanner):
            def __init__(self):
                super().__init__((need("core", "no_such_capability",
                                       produces=("STATISTICAL_RESULT",)),))
                self.add_needs_calls = 0

            def add_needs(self, **kwargs):
                self.add_needs_calls += 1
                return ()

        runtime, _executor = build_test_runtime(needs=())
        planner = RecordingReplanPlanner()
        runtime._planner = planner  # noqa: SLF001
        result = runtime.send_message(runtime.start_conversation(), "unresolved request")
        self.assertGreaterEqual(planner.add_needs_calls, 1)
        self.assertFalse(result.coverage.core_goal_supported)

    def test_verified_obligation_credits_a_closed_core_need(self):
        obligation = UserObligation(obligation_id="o1", kind="SEASON", value="2025",
                                    description="season 2025")
        goal = Goal(goal_id="g", statement="season", obligations=(obligation,))
        core = Need(need_id="core", objective="fail", criticality="CORE",
                    proposed_capability="local_analytics")
        other = Need(need_id="other", objective="ok", criticality="CORE",
                     proposed_capability="local_analytics")
        core_assessment = CoverageAssessment(assessment_id="a", goal_id="g",
                                             need_id="core", verdict="UNSATISFIED")
        other_assessment = CoverageAssessment(assessment_id="b", goal_id="g",
                                              need_id="other", verdict="SATISFIED")
        coverage = CoverageJudge().summarize(
            goal, (core, other), (core_assessment, other_assessment),
            obligation_coverage={"o1": "VERIFIED"}, closed_needs=("core",))
        self.assertTrue(coverage.core_goal_supported)


class MultilingualConvergenceTests(unittest.TestCase):
    @staticmethod
    def _obligation_signature(message):
        obligations = extract_obligations(message=message,
                                          brief=SemanticBrief(brief_id="b"))
        return {(item.kind, item.value) for item in obligations}

    def test_english_and_chinese_paraphrases_share_typed_obligations(self):
        english = self._obligation_signature("2025 batters with at least 20")
        chinese = self._obligation_signature("2025年 打者 至少 20")
        for expected in (("SEASON", "2025"), ("QUALIFICATION", ">=20")):
            self.assertIn(expected, english)
            self.assertIn(expected, chinese)
        self.assertTrue({("POPULATION", "batters")} & english)
        self.assertTrue({("POPULATION", "batters")} & chinese)

    def test_conflict_detection_is_language_independent(self):
        english = detect_conflicts(
            scope=None, obligations=(
                UserObligation(obligation_id="a", kind="QUALIFICATION",
                               description="x", value=">=50"),
                UserObligation(obligation_id="b", kind="QUALIFICATION",
                               description="y", value="<=10")),
            today=TODAY)
        chinese = detect_conflicts(
            scope=None, obligations=(
                UserObligation(obligation_id="a", kind="QUALIFICATION",
                               description="至少50", value=">=50"),
                UserObligation(obligation_id="b", kind="QUALIFICATION",
                               description="最多10", value="<=10")),
            today=TODAY)
        self.assertEqual([item.kind for item in english],
                         [item.kind for item in chinese])

    def test_capability_shaped_and_concrete_params_normalize_equally(self):
        from app.artifact_runtime.planner import normalize_tool_inputs
        shaped = normalize_tool_inputs({"TEAM_NAME": "New York Yankees",
                                        "SEARCH_QUERY": "roster"})
        concrete = normalize_tool_inputs({"team": "New York Yankees",
                                          "query": "roster"})
        self.assertEqual(shaped, concrete)


if __name__ == "__main__":
    unittest.main()