"""Tests for the dual semantic runtime: anchors, reconciler, reviewer and parser.

The important invariant is not identical JSON between roles, but that an incorrect or
under-specified interpretation cannot reach execution. These tests use scripted providers
and never touch the network.
"""

import json
import unittest

from app.llm.provider import FakeModelProvider, ProviderError
from app.models.contracts import CountConstraint, QualificationConstraint, RankingConstraint
from app.models.semantic_review import SemanticReviewResult
from app.persistence.semantic_review import SemanticReviewStore
from app.persistence.store import SqliteOperationalStore
from app.semantic.dual_parser import (DualSemanticParser, MATERIAL_DISAGREEMENT,
                                      SEMANTIC_UNAVAILABLE, needs_dual_review)
from app.semantic.lexical_anchors import extract_lexical_anchors
from app.semantic.semantic_extractor import (LLMSemanticExtractor, LLMSemanticReviewer,
                                             SemanticVocabulary)
from app.semantic.semantic_reconciler import (SemanticReconciler, reconcile_with_anchors)
from app.semantic.semantic_validator import SemanticValidationError, validate_candidate

VOCABULARY = SemanticVocabulary()
COMPOUND = ("top 5 by maximum exit velocity on fastballs at least 95 mph "
            "with at least 100 BBE in 2025")

NUMERIC = {"kind": "NUMERIC", "metric": "pitch_velocity", "operator": "GTE", "value": 95,
           "unit": "mph", "origin": "USER_EXPLICIT",
           "evidence": {"text": "fastballs at least 95 mph"}}
RANKING = {"kind": "RANKING", "metric_key": "exit_velocity", "aggregation": "MAX",
           "direction": "DESC", "limit": 5, "origin": "USER_EXPLICIT",
           "evidence": {"text": "top 5 by maximum exit velocity"}}
QUALIFICATION = {"kind": "QUALIFICATION", "min_batted_balls": 100, "origin": "USER_EXPLICIT",
                 "evidence": {"text": "at least 100 BBE"}}


def candidate(*constraints, ambiguities=()):
    return json.dumps({"constraints": list(constraints), "ambiguities": list(ambiguities)})


def parser_with(extractor_responses, reviewer_responses):
    return DualSemanticParser(
        LLMSemanticExtractor(FakeModelProvider(responses=extractor_responses), "extractor"),
        LLMSemanticReviewer(FakeModelProvider(responses=reviewer_responses), "reviewer"),
        extractor_model="extractor", reviewer_model="reviewer")


# -- Lexical anchors ---------------------------------------------------------


class LexicalAnchorTests(unittest.TestCase):
    def test_detects_explicit_facts(self):
        anchors = extract_lexical_anchors(COMPOUND)
        self.assertEqual([item.key for item in anchors.numerics()], ["pitch_velocity"])
        self.assertEqual(anchors.ranking_limits[0].limit, 5)
        self.assertEqual(anchors.qualification_phrases[0].value, 100)

    def test_spelled_number_is_detected_but_not_invented(self):
        anchors = extract_lexical_anchors(
            "top 5 by maximum exit velocity with at least twenty batted balls in 2025")
        self.assertEqual(len(anchors.qualification_phrases), 1)
        self.assertIsNone(anchors.qualification_phrases[0].value)

    def test_conflicting_ranking_limits_are_visible(self):
        anchors = extract_lexical_anchors("top 5 or top 10 by maximum exit velocity in 2025")
        self.assertEqual({item.limit for item in anchors.ranking_limits}, {5, 10})

    def test_explicit_population_is_independent_of_other_constraints(self):
        anchors = extract_lexical_anchors("exhibition games")
        self.assertEqual(anchors.population.game_types, ("EXHIBITION",))

    def test_ambiguous_location_cue_is_detected_alone(self):
        anchors = extract_lexical_anchors("Show me hitters against high fastballs.")
        self.assertTrue(anchors.location_ambiguity)
        self.assertTrue(needs_dual_review(anchors))

    def test_knowledge_question_does_not_require_dual_review(self):
        self.assertFalse(needs_dual_review(extract_lexical_anchors("DFA是什么意思？")))


# -- Anchor reconciliation ---------------------------------------------------


class AnchorReconciliationTests(unittest.TestCase):
    def _differences(self, query, fields):
        from app.models.semantic_candidate import SemanticCandidate
        candidate = SemanticCandidate.model_validate({"constraints": [fields]})
        canonical = []
        for item in candidate.constraints:
            from app.semantic.semantic_validator import _canonical
            canonical.append(_canonical(item, VOCABULARY))
        return reconcile_with_anchors(tuple(canonical), extract_lexical_anchors(query))

    def test_contradictory_numeric_operator_is_material(self):
        diffs = self._differences("fastballs at least 95 mph", {
            "kind": "NUMERIC", "metric": "pitch_velocity", "operator": "LTE", "value": 20,
            "unit": "mph", "evidence": {"text": "fastballs at least 95 mph"}})
        self.assertTrue(any(item.code == "ANCHOR_CONFLICT" for item in diffs))

    def test_dropped_qualification_is_material(self):
        diffs = self._differences("top 5 by maximum exit velocity with at least 100 BBE", {
            "kind": "RANKING", "metric_key": "exit_velocity", "aggregation": "MAX",
            "direction": "DESC", "limit": 5, "evidence": {"text": "top 5"}})
        self.assertTrue(any(item.code == "ANCHOR_MISSING"
                            and item.dimension == "qualification" for item in diffs))

    def test_dropped_explicit_population_is_material(self):
        query = "top 5 by maximum exit velocity over all pitches in 2025"
        diffs = self._differences(query, {
            "kind": "RANKING", "metric_key": "exit_velocity", "aggregation": "MAX",
            "direction": "DESC", "limit": 5, "evidence": {"text": "top 5"}})
        self.assertTrue(any(item.code == "ANCHOR_MISSING"
                            and item.dimension == "population" for item in diffs))

    def test_query_level_ranking_conflict_is_not_recoverable(self):
        query = "top 5 or top 10 by maximum exit velocity in 2025"
        diffs = self._differences(query, {
            "kind": "RANKING", "metric_key": "exit_velocity", "aggregation": "MAX",
            "direction": "DESC", "limit": 5, "evidence": {"text": "top 5"}})
        ambiguous = [item for item in diffs if item.code == "AMBIGUOUS_RANKING"]
        self.assertTrue(ambiguous)
        self.assertFalse(ambiguous[0].recoverable)


# -- Candidate A vs Candidate B ---------------------------------------------


class ReconcilerTests(unittest.TestCase):
    def setUp(self):
        self.reconciler = SemanticReconciler()
        from app.models.semantic_candidate import SemanticCandidate
        self.build = lambda *fields: SemanticCandidate.model_validate(
            {"constraints": list(fields)})

    def test_material_disagreement_on_aggregation(self):
        a = self.build(RANKING)
        b = self.build(dict(RANKING, aggregation="AVG"))
        result = self.reconciler.compare(a, b, extract_lexical_anchors(COMPOUND))
        self.assertEqual(result.agreement_status, "MATERIAL_DISAGREEMENT")
        self.assertFalse(result.safe_to_execute)

    def test_material_disagreement_on_game_type(self):
        a = self.build({"kind": "POPULATION", "game_types": ["REGULAR_SEASON"],
                        "event_population": "BATTED_BALL", "origin": "USER_EXPLICIT"})
        b = self.build({"kind": "POPULATION", "game_types": ["POSTSEASON"],
                        "event_population": "BATTED_BALL", "origin": "USER_EXPLICIT"})
        result = self.reconciler.compare(a, b, extract_lexical_anchors("top 5 in the postseason"))
        self.assertEqual(result.agreement_status, "MATERIAL_DISAGREEMENT")

    def test_material_disagreement_on_location_definition(self):
        a = self.build({"kind": "LOCATION", "definition": "ZONE_UPPER_THIRD",
                        "origin": "USER_EXPLICIT"})
        b = self.build({"kind": "LOCATION", "definition": "ZONE_UPPER_OUTSIDE",
                        "origin": "USER_EXPLICIT"})
        result = self.reconciler.compare(a, b, extract_lexical_anchors("top 5 by exit velocity"))
        self.assertEqual(result.agreement_status, "MATERIAL_DISAGREEMENT")

    def test_ambiguous_location_takes_precedence_over_a_location_difference(self):
        a = self.build({"kind": "LOCATION", "definition": "ZONE_UPPER_THIRD",
                        "origin": "USER_EXPLICIT"})
        b = self.build({"kind": "LOCATION", "definition": "ZONE_UPPER_OUTSIDE",
                        "origin": "USER_EXPLICIT"})
        result = self.reconciler.compare(a, b, extract_lexical_anchors("high fastballs"))
        self.assertEqual(result.agreement_status, "AMBIGUOUS")

    def test_equivalent_wording_and_offsets_agree(self):
        a = self.build(dict(NUMERIC, evidence={"text": "at least 95 mph", "start": 0, "end": 13}))
        b = self.build(dict(NUMERIC, evidence={"text": "95 mph", "start": 10, "end": 16}))
        result = self.reconciler.compare(a, b, extract_lexical_anchors("fastballs at least 95 mph"))
        self.assertEqual(result.agreement_status, "AGREE")
        self.assertTrue(result.safe_to_execute)

    def test_default_only_omission_is_not_material(self):
        a = self.build(RANKING, {"kind": "POPULATION", "game_types": ["REGULAR_SEASON"],
                                 "event_population": "BATTED_BALL", "origin": "SYSTEM_INFERRED"})
        b = self.build(RANKING)
        result = self.reconciler.compare(a, b, extract_lexical_anchors("top 5 by exit velocity"))
        self.assertEqual(result.agreement_status, "AGREE")

    def test_ambiguity_from_anchors_forces_clarification(self):
        a = self.build(RANKING)
        b = self.build(RANKING)
        result = self.reconciler.compare(a, b, extract_lexical_anchors("top 5 on high fastballs"))
        self.assertEqual(result.agreement_status, "AMBIGUOUS")


# -- Dual parser -------------------------------------------------------------


class DualParserTests(unittest.TestCase):
    def test_agreement_executes_canonical_semantics(self):
        parser = parser_with([candidate(NUMERIC, RANKING, QUALIFICATION)],
                             [candidate(NUMERIC, RANKING, QUALIFICATION)])
        result = parser.parse(COMPOUND)
        self.assertEqual(result.clarification_reason, "")
        self.assertEqual(result.review.agreement_status, "AGREE")
        self.assertEqual(result.review.outcome, "DUAL_REVIEW")
        self.assertTrue(any(isinstance(item, RankingConstraint) for item in result.constraints))

    def test_numeric_disagreement_never_executes(self):
        bad = dict(NUMERIC, operator="LTE", value=20)
        parser = parser_with([candidate(NUMERIC, RANKING, QUALIFICATION)],
                             [candidate(bad, RANKING, QUALIFICATION)])
        result = parser.parse(COMPOUND)
        self.assertEqual(result.clarification_reason, MATERIAL_DISAGREEMENT)
        self.assertEqual(result.constraints, ())

    def test_explicit_all_pitches_omission_clarifies(self):
        query = "Rank hitters by maximum exit velocity over all pitches in 2025"
        parser = parser_with([candidate(RANKING)], [candidate(RANKING)])
        result = parser.parse(query)
        self.assertTrue(result.failed_closed)

    def test_spelled_qualification_under_provider_failure_clarifies(self):
        query = ("top 5 by maximum exit velocity with at least twenty batted balls in 2025")
        parser = DualSemanticParser(
            LLMSemanticExtractor(FakeModelProvider(error=ProviderError("down")), "a"),
            LLMSemanticReviewer(FakeModelProvider(error=ProviderError("down")), "b"))
        result = parser.parse(query)
        self.assertEqual(result.clarification_reason, SEMANTIC_UNAVAILABLE)

    def test_single_reader_uses_deterministic_high_confidence_path(self):
        parser = DualSemanticParser(
            LLMSemanticExtractor(FakeModelProvider(responses=[candidate(NUMERIC, RANKING,
                                                                        QUALIFICATION)]), "a"),
            LLMSemanticReviewer(FakeModelProvider(error=ProviderError("down")), "b"))
        result = parser.parse(COMPOUND)
        self.assertEqual(result.clarification_reason, "")
        self.assertEqual(result.review.agreement_status, "REVIEWER_UNAVAILABLE")
        qualification = next(item for item in result.constraints
                             if isinstance(item, QualificationConstraint))
        self.assertEqual(qualification.min_batted_balls, 100)

    def test_both_readers_unavailable_clarifies(self):
        parser = DualSemanticParser(
            LLMSemanticExtractor(FakeModelProvider(error=ProviderError("down")), "a"),
            LLMSemanticReviewer(FakeModelProvider(error=ProviderError("down")), "b"))
        result = parser.parse(COMPOUND)
        self.assertEqual(result.clarification_reason, SEMANTIC_UNAVAILABLE)
        self.assertEqual(result.review.agreement_status, "BOTH_UNAVAILABLE")

    def test_location_ambiguity_is_not_suppressed(self):
        parser = parser_with(
            [candidate(NUMERIC, RANKING,
                       {"kind": "LOCATION", "definition": "BATTER_RELATIVE_UPPER_EDGE",
                        "origin": "USER_EXPLICIT", "evidence": {"text": "high fastballs"}},
                       ambiguities=[{"kind": "location.upper_edge",
                                     "evidence": {"text": "high fastballs"}}])],
            [candidate(NUMERIC, RANKING,
                       {"kind": "LOCATION", "definition": "ZONE_UPPER_THIRD",
                        "origin": "USER_EXPLICIT", "evidence": {"text": "high fastballs"}})])
        result = parser.parse("top 5 by exit velocity on high fastballs")
        self.assertTrue(result.failed_closed or result.location_wording_requested)

    def test_compound_count_states_are_preserved(self):
        query = "top 5 by maximum exit velocity on 0-2 or 1-1 counts in 2025"
        count = {"kind": "COUNT", "origin": "USER_EXPLICIT",
                 "states": [{"balls": 0, "strikes": 2}, {"balls": 1, "strikes": 1}],
                 "evidence": {"text": "0-2 or 1-1"}}
        parser = parser_with([candidate(RANKING, count)], [candidate(RANKING, count)])
        result = parser.parse(query)
        item = next(c for c in result.constraints if isinstance(c, CountConstraint))
        self.assertEqual(item.exact_states, ((0, 2), (1, 1)))

    def test_knowledge_query_never_calls_the_models(self):
        provider_a = FakeModelProvider(responses=[])
        provider_b = FakeModelProvider(responses=[])
        result = DualSemanticParser(LLMSemanticExtractor(provider_a, "a"),
                                    LLMSemanticReviewer(provider_b, "b")).parse("DFA是什么意思？")
        self.assertEqual(result.constraints, ())
        self.assertEqual(provider_a.calls, [])
        self.assertEqual(provider_b.calls, [])

    def test_review_record_is_reused_on_replay(self):
        store = SqliteOperationalStore(":memory:")
        review_store = SemanticReviewStore(store)
        extractor_provider = FakeModelProvider(responses=[candidate(NUMERIC, RANKING, QUALIFICATION)])
        reviewer_provider = FakeModelProvider(responses=[candidate(NUMERIC, RANKING, QUALIFICATION)])
        parser = DualSemanticParser(
            LLMSemanticExtractor(extractor_provider, "a"),
            LLMSemanticReviewer(reviewer_provider, "b"), review_store=review_store)
        parser.parse(COMPOUND)
        parser.parse(COMPOUND)
        self.assertEqual(len(extractor_provider.calls), 1)
        self.assertEqual(len(reviewer_provider.calls), 1)
        store.close()

    def test_review_record_persists_a_disagreement(self):
        store = SqliteOperationalStore(":memory:")
        review_store = SemanticReviewStore(store)
        bad = dict(NUMERIC, operator="LTE", value=20)
        parser = DualSemanticParser(
            LLMSemanticExtractor(FakeModelProvider(responses=[
                candidate(NUMERIC, RANKING, QUALIFICATION)]), "a"),
            LLMSemanticReviewer(FakeModelProvider(responses=[
                candidate(bad, RANKING, QUALIFICATION)]), "b"), review_store=review_store)
        first = parser.parse(COMPOUND)
        second = parser.parse(COMPOUND)
        self.assertEqual(first.clarification_reason, MATERIAL_DISAGREEMENT)
        self.assertEqual(second.clarification_reason, MATERIAL_DISAGREEMENT)
        store.close()

    def test_review_result_is_bounded(self):
        parser = parser_with([candidate(NUMERIC, RANKING, QUALIFICATION)],
                             [candidate(NUMERIC, RANKING, QUALIFICATION)])
        review = parser.parse(COMPOUND).review
        self.assertIsInstance(review, SemanticReviewResult)
        self.assertEqual(review.extractor_model, "extractor")
        self.assertEqual(review.reviewer_model, "reviewer")
        self.assertEqual(len(review.calls), 2)
        self.assertTrue(all(call.latency_ms >= 0 for call in review.calls))


class ValidatorAnchorIntegrationTests(unittest.TestCase):
    def test_wrong_operator_is_rejected(self):
        from app.models.semantic_candidate import SemanticCandidate
        candidate_model = SemanticCandidate.model_validate({"constraints": [dict(
            NUMERIC, operator="LTE", value=20, evidence={"text": "fastballs at least 95 mph"})]})
        with self.assertRaises(SemanticValidationError):
            validate_candidate(candidate_model, "fastballs at least 95 mph", VOCABULARY)


if __name__ == "__main__":
    unittest.main()
