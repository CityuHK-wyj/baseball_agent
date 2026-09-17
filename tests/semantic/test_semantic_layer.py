"""Tests for the hybrid semantic layer: extractor, validator, fallback.

Two layers are tested separately:

* the extractor turns natural language into a typed ``SemanticCandidate``;
* the deterministic validator decides whether a candidate may become canonical.
"""

import json
import unittest

from app.llm.prompts import SEMANTIC_PROMPT
from app.llm.provider import FakeModelProvider, ProviderError, ProviderTimeout
from app.models.contracts import (DEFAULT_RANKING_LIMIT, CountConstraint, NumericConstraint,
                                  PopulationConstraint, QualificationConstraint,
                                  RankingConstraint)
from app.models.semantic_candidate import (CandidateConstraint, EvidenceSpan,
                                           SemanticCandidate)
from app.semantic.hybrid_parser import HybridSemanticParser
from app.semantic.entity_resolver import EntityDictionary, EntityResolver
from app.semantic.normalizer import SemanticNormalizer
from app.semantic.objective_extractor import RuleBasedObjectiveExtractor
from app.semantic.semantic_extractor import (DeterministicSemanticExtractor,
                                             LLMSemanticExtractor, SemanticProviderError,
                                             SemanticSchemaError, SemanticVocabulary)
from app.semantic.semantic_validator import (SemanticValidationError, validate_candidate)


def _constraint(candidate_constraints, kind):
    return next(item for item in candidate_constraints if item.kind == kind)


def _canonical(result, cls):
    return next(item for item in result.constraints if isinstance(item, cls))


# -- Extractor layer ---------------------------------------------------------


class DeterministicExtractorTests(unittest.TestCase):
    def setUp(self):
        self.extractor = DeterministicSemanticExtractor()
        self.vocabulary = SemanticVocabulary()

    def test_extracts_typed_numeric_with_evidence(self):
        candidate = self.extractor.extract(
            "top 5 by exit velocity on fastballs at least 95 mph in 2023", self.vocabulary)
        numeric = _constraint(candidate.constraints, "NUMERIC")
        self.assertEqual((numeric.metric, numeric.operator, numeric.value),
                         ("pitch_velocity", "GTE", 95.0))
        self.assertIn("95 mph", numeric.evidence.text)

    def test_extracts_exact_compound_count_states(self):
        candidate = self.extractor.extract(
            "top 5 by exit velocity on 0-2 or 1-1 counts in 2023", self.vocabulary)
        count = _constraint(candidate.constraints, "COUNT")
        self.assertEqual({(s.balls, s.strikes) for s in count.states}, {(0, 2), (1, 1)})

    def test_qualification_is_not_a_velocity(self):
        candidate = self.extractor.extract(
            "top 5 by maximum exit velocity on fastballs at least 95 mph, at least 100 BBE",
            self.vocabulary)
        numerics = [c for c in candidate.constraints if c.kind == "NUMERIC"]
        self.assertEqual([(c.metric, c.value) for c in numerics], [("pitch_velocity", 95.0)])
        self.assertEqual(_constraint(candidate.constraints, "QUALIFICATION").min_batted_balls, 100)

    def test_exhibition_population_candidate(self):
        candidate = self.extractor.extract(
            "top 5 by maximum exit velocity in exhibition games in 2023", self.vocabulary)
        self.assertEqual(_constraint(candidate.constraints, "POPULATION").game_types,
                         ("EXHIBITION",))

    def test_ambiguous_location_becomes_an_ambiguity_candidate(self):
        candidate = self.extractor.extract(
            "top 5 by exit velocity on fastballs near the upper edge in 2023", self.vocabulary)
        self.assertTrue(any(item.kind.startswith("location") for item in candidate.ambiguities))


# -- Validator layer ---------------------------------------------------------


class ValidatorTests(unittest.TestCase):
    def setUp(self):
        self.vocabulary = SemanticVocabulary()

    def _validate(self, candidate, query):
        return validate_candidate(candidate, query, self.vocabulary)

    def test_valid_candidate_becomes_canonical_constraints(self):
        result = self._validate(SemanticCandidate(constraints=(
            CandidateConstraint(kind="NUMERIC", metric="pitch_velocity", operator="GTE",
                                value=95.0, unit="mph",
                                evidence=EvidenceSpan(text="fastballs at least 95 mph")),
            CandidateConstraint(kind="RANKING", metric_key="exit_velocity", aggregation="MAX",
                                direction="DESC", limit=5,
                                evidence=EvidenceSpan(text="top 5")),
        )), "top 5 on fastballs at least 95 mph")
        velocity = _canonical(result, NumericConstraint)
        self.assertEqual((velocity.key, velocity.value), ("pitch_velocity", 95.0))
        self.assertEqual(_canonical(result, RankingConstraint).aggregation, "MAX")

    def test_qualification_number_bound_to_velocity_is_rejected(self):
        candidate = SemanticCandidate(constraints=(
            CandidateConstraint(kind="NUMERIC", metric="pitch_velocity", operator="GTE",
                                value=20.0, unit="mph",
                                evidence=EvidenceSpan(text="20 BBE")),))
        with self.assertRaises(SemanticValidationError) as caught:
            self._validate(candidate, "at least 20 BBE")
        self.assertEqual(caught.exception.code, "EVIDENCE_METRIC_MISMATCH")

    def test_evidence_claimed_by_two_clauses_is_rejected(self):
        shared = EvidenceSpan(text="20 BBE")
        candidate = SemanticCandidate(constraints=(
            CandidateConstraint(kind="NUMERIC", metric="exit_velocity", operator="GTE",
                                value=20.0, unit="mph", evidence=shared),
            CandidateConstraint(kind="QUALIFICATION", min_batted_balls=20, evidence=shared),))
        with self.assertRaises(SemanticValidationError) as caught:
            self._validate(candidate, "at least 20 BBE")
        self.assertEqual(caught.exception.code, "DUPLICATE_EVIDENCE_OWNERSHIP")
        self.assertFalse(caught.exception.recoverable)

    def test_ungrounded_explicit_evidence_is_rejected(self):
        candidate = SemanticCandidate(constraints=(
            CandidateConstraint(kind="NUMERIC", metric="pitch_velocity", operator="GTE",
                                value=95.0, unit="mph",
                                evidence=EvidenceSpan(text="not in the query")),))
        with self.assertRaises(SemanticValidationError) as caught:
            self._validate(candidate, "top 5 by exit velocity")
        self.assertEqual(caught.exception.code, "EVIDENCE_NOT_GROUNDED")

    def test_unsupported_metric_is_rejected(self):
        candidate = SemanticCandidate(constraints=(
            CandidateConstraint(kind="NUMERIC", metric="spin_rate", operator="GTE", value=2400.0,
                                unit="rpm", evidence=EvidenceSpan(text="2400 rpm")),))
        with self.assertRaises(SemanticValidationError) as caught:
            self._validate(candidate, "2400 rpm")
        self.assertEqual(caught.exception.code, "UNSUPPORTED_METRIC")

    def test_unsupported_aggregation_is_rejected_by_a_restricted_vocabulary(self):
        restricted = SemanticVocabulary(aggregations=("AVG",))
        candidate = SemanticCandidate(constraints=(
            CandidateConstraint(kind="RANKING", metric_key="exit_velocity", aggregation="MAX",
                                direction="DESC", limit=5,
                                evidence=EvidenceSpan(text="top 5")),))
        with self.assertRaises(SemanticValidationError) as caught:
            validate_candidate(candidate, "top 5 by maximum exit velocity", restricted)
        self.assertEqual(caught.exception.code, "UNSUPPORTED_AGGREGATION")

    def test_count_outside_vocabulary_range_is_rejected(self):
        restricted = SemanticVocabulary(max_balls=1)
        candidate = SemanticCandidate(constraints=(
            CandidateConstraint(kind="COUNT", strikes=2, balls=(3,),
                                evidence=EvidenceSpan(text="3-2")),))
        with self.assertRaises(SemanticValidationError) as caught:
            validate_candidate(candidate, "3-2 fastballs", restricted)
        self.assertEqual(caught.exception.code, "MISSING_FIELD")

    def test_contradictory_aggregation_fails_closed(self):
        candidate = SemanticCandidate(constraints=(
            CandidateConstraint(kind="RANKING", metric_key="exit_velocity", aggregation="MAX",
                                direction="DESC", limit=5,
                                evidence=EvidenceSpan(text="maximum exit velocity")),
            CandidateConstraint(kind="RANKING", metric_key="exit_velocity", aggregation="AVG",
                                direction="DESC", limit=5,
                                evidence=EvidenceSpan(text="average exit velocity")),))
        with self.assertRaises(SemanticValidationError) as caught:
            self._validate(candidate, "top 5 by maximum exit velocity and average exit velocity")
        self.assertEqual(caught.exception.code, "CONTRADICTORY_AGGREGATION")
        self.assertFalse(caught.exception.recoverable)

    def test_explicit_population_beats_an_inferred_default(self):
        candidate = SemanticCandidate(constraints=(
            CandidateConstraint(kind="RANKING", metric_key="exit_velocity", aggregation="MAX",
                                direction="DESC", limit=5,
                                evidence=EvidenceSpan(text="top 5")),
            CandidateConstraint(kind="POPULATION", game_types=("EXHIBITION",),
                                event_population="BATTED_BALL",
                                evidence=EvidenceSpan(text="exhibition games")),
            CandidateConstraint(kind="POPULATION", game_types=("REGULAR_SEASON",),
                                event_population="BATTED_BALL", origin="SYSTEM_INFERRED"),))
        result = self._validate(candidate, "top 5 by maximum exit velocity in exhibition games")
        populations = [item for item in result.constraints
                       if isinstance(item, PopulationConstraint)]
        self.assertEqual(len(populations), 1)
        self.assertEqual(populations[0].game_types, ("EXHIBITION",))

    def test_exit_velocity_over_all_pitches_is_rejected_recoverably(self):
        candidate = SemanticCandidate(constraints=(
            CandidateConstraint(kind="RANKING", metric_key="exit_velocity", aggregation="MAX",
                                direction="DESC", limit=5,
                                evidence=EvidenceSpan(text="maximum exit velocity")),
            CandidateConstraint(kind="POPULATION", game_types=("REGULAR_SEASON",),
                                event_population="ALL_PITCHES",
                                evidence=EvidenceSpan(text="all pitches")),))
        with self.assertRaises(SemanticValidationError) as caught:
            self._validate(candidate,
                           "rank hitters by maximum exit velocity over all pitches")
        self.assertEqual(caught.exception.code, "INCOMPATIBLE_EVENT_POPULATION")
        self.assertTrue(caught.exception.recoverable)

    def test_absent_ranking_limit_uses_the_documented_default(self):
        candidate = SemanticCandidate(constraints=(
            CandidateConstraint(kind="RANKING", metric_key="exit_velocity", aggregation="MAX",
                                direction="DESC", evidence=EvidenceSpan(text="rank hitters")),))
        result = self._validate(candidate, "rank hitters by maximum exit velocity")
        self.assertEqual(_canonical(result, RankingConstraint).limit, DEFAULT_RANKING_LIMIT)

    def test_absent_population_receives_a_documented_default_after_validation(self):
        candidate = SemanticCandidate(constraints=(
            CandidateConstraint(kind="RANKING", metric_key="exit_velocity", aggregation="MAX",
                                direction="DESC", limit=5,
                                evidence=EvidenceSpan(text="top 5")),))
        result = self._validate(candidate, "top 5 by maximum exit velocity")
        population = _canonical(result, PopulationConstraint)
        self.assertEqual(population.game_types, ("REGULAR_SEASON",))
        self.assertEqual(population.origin, "SYSTEM_INFERRED")

    def test_compound_count_is_preserved_exactly(self):
        # Build through the public schema to get typed CountState instances.
        candidate = SemanticCandidate.model_validate({"constraints": [{
            "kind": "COUNT",
            "states": [{"balls": 0, "strikes": 2}, {"balls": 1, "strikes": 1}],
            "evidence": {"text": "0-2 or 1-1"}}]})
        result = self._validate(candidate, "top 5 by exit velocity on 0-2 or 1-1 counts")
        count = _canonical(result, CountConstraint)
        self.assertEqual(count.exact_states, ((0, 2), (1, 1)))


# -- LLM extractor (mocked provider) ----------------------------------------


def _llm_json(**overrides):
    payload = {
        "constraints": [{
            "kind": "NUMERIC", "metric": "pitch_velocity", "operator": "GTE", "value": 95.0,
            "unit": "mph", "evidence": {"text": "fastballs at least 95 mph"},
            "origin": "USER_EXPLICIT",
        }],
        "ambiguities": [],
    }
    payload.update(overrides)
    return json.dumps(payload)


class LLMExtractorTests(unittest.TestCase):
    def setUp(self):
        self.vocabulary = SemanticVocabulary()

    def test_valid_json_becomes_a_candidate(self):
        provider = FakeModelProvider(responses=[_llm_json()])
        candidate = LLMSemanticExtractor(provider, "test-model").extract(
            "top 5 on fastballs at least 95 mph", self.vocabulary)
        self.assertEqual(candidate.extractor, "llm")
        self.assertEqual(_constraint(candidate.constraints, "NUMERIC").metric, "pitch_velocity")

    def test_fenced_json_is_accepted(self):
        provider = FakeModelProvider(responses=[f"```json\n{_llm_json()}\n```"])
        candidate = LLMSemanticExtractor(provider, "test-model").extract(
            "top 5 on fastballs at least 95 mph", self.vocabulary)
        self.assertEqual(len(candidate.constraints), 1)

    def test_invalid_json_raises_schema_error(self):
        provider = FakeModelProvider(responses=["this is not json"])
        with self.assertRaises(SemanticSchemaError):
            LLMSemanticExtractor(provider, "test-model").extract("query", self.vocabulary)

    def test_unknown_field_is_rejected_by_the_closed_schema(self):
        provider = FakeModelProvider(responses=[json.dumps({"constraints": [],
                                                            "sql": "SELECT 1"})])
        with self.assertRaises(SemanticSchemaError):
            LLMSemanticExtractor(provider, "test-model").extract("query", self.vocabulary)

    def test_provider_error_is_normalized(self):
        provider = FakeModelProvider(error=ProviderTimeout("timed out"))
        with self.assertRaises(SemanticProviderError):
            LLMSemanticExtractor(provider, "test-model").extract("query", self.vocabulary)

    def test_prompt_carries_no_physical_columns(self):
        provider = FakeModelProvider(responses=[_llm_json()])
        LLMSemanticExtractor(provider, "test-model").extract(
            "top 5 on fastballs at least 95 mph", self.vocabulary)
        prompt = provider.calls[0][1]
        for physical in ("release_speed", "launch_speed", "SELECT", "read_parquet"):
            self.assertNotIn(physical, prompt)

    def test_prompt_publishes_the_closed_origin_vocabulary(self):
        prompt = SEMANTIC_PROMPT.render(
            query="q", vocabulary_json=self.vocabulary.as_prompt_json())
        self.assertIn("USER_EXPLICIT", prompt)
        self.assertIn("\"origins\"", self.vocabulary.as_prompt_json())

    def test_observed_live_shape_is_normalized_then_validated(self):
        # Shape observed from the live model: a casually spelled origin, null fields for
        # inapplicable keys, a ranking metric under ``metric`` and no stated limit.
        provider = FakeModelProvider(responses=[json.dumps({"constraints": [
            {"kind": "NUMERIC", "metric": "pitch_velocity", "operator": "GTE",
             "value": 95, "unit": "mph", "origin": "explicit",
             "states": None, "balls": None, "game_types": None,
             "evidence": {"text": "fastballs at least 95 mph"}},
            {"kind": "RANKING", "metric": "exit_velocity", "aggregation": "MAX",
             "direction": "DESC", "origin": "user",
             "evidence": {"text": "rank hitters by maximum exit velocity"}},
            {"kind": "QUALIFICATION", "min_batted_balls": 100, "origin": "explicit",
             "evidence": {"text": "at least 100 BBE"}},
        ], "ambiguities": []})])
        query = ("On fastballs at least 95 mph, rank hitters by maximum exit velocity "
                 "with at least 100 BBE.")
        parsed = HybridSemanticParser(extractor=LLMSemanticExtractor(
            provider, "test-model")).parse(query)
        self.assertEqual(parsed.extractor, "llm")
        self.assertEqual(parsed.fallback_reason, "")
        self.assertEqual(parsed.clarification_reason, "")
        numeric = _canonical(parsed, NumericConstraint)
        self.assertEqual((numeric.key, numeric.operator, numeric.value),
                         ("pitch_velocity", "GTE", 95.0))
        ranking = _canonical(parsed, RankingConstraint)
        self.assertEqual((ranking.metric_key, ranking.aggregation, ranking.limit),
                         ("exit_velocity", "MAX", DEFAULT_RANKING_LIMIT))
        self.assertEqual(_canonical(parsed, QualificationConstraint).min_batted_balls, 100)

    def test_drifting_offsets_are_reconciled_against_the_query(self):
        query = ("On fastballs at least 95 mph, rank hitters by maximum exit velocity "
                 "with at least 100 BBE.")
        provider = FakeModelProvider(responses=[json.dumps({"constraints": [
            {"kind": "QUALIFICATION", "min_batted_balls": 100, "origin": "USER_EXPLICIT",
             "evidence": {"text": "at least 100 BBE", "start": 999, "end": 1014}},
        ]})])
        candidate = LLMSemanticExtractor(provider, "test-model").extract(
            query, self.vocabulary)
        evidence = candidate.constraints[0].evidence
        self.assertEqual(query[evidence.start:evidence.end], "at least 100 BBE")

    def test_unknown_origin_is_still_rejected(self):
        provider = FakeModelProvider(responses=[json.dumps({"constraints": [
            {"kind": "QUALIFICATION", "min_batted_balls": 20, "origin": "guessed",
             "evidence": {"text": "20 BBE"}}]})])
        with self.assertRaises(SemanticSchemaError):
            LLMSemanticExtractor(provider, "test-model").extract(
                "at least 20 BBE", self.vocabulary)


# -- Hybrid fallback ---------------------------------------------------------


class HybridParserTests(unittest.TestCase):
    def test_deterministic_primary_still_validates(self):
        result = HybridSemanticParser().parse("top 5 by maximum exit velocity in 2023")
        self.assertEqual(_canonical(result, RankingConstraint).aggregation, "MAX")
        self.assertEqual(result.fallback_reason, "")

    def test_provider_failure_falls_back_without_dropping_constraints(self):
        primary = LLMSemanticExtractor(FakeModelProvider(error=ProviderError("down")),
                                       "test-model")
        result = HybridSemanticParser(extractor=primary).parse(
            "top 5 by maximum exit velocity on fastballs at least 95 mph, at least 100 BBE")
        self.assertEqual(result.extractor, "deterministic")
        self.assertIn("SemanticProviderError", result.fallback_reason)
        numerics = [item for item in result.constraints if isinstance(item, NumericConstraint)]
        self.assertEqual([(item.key, item.value) for item in numerics], [("pitch_velocity", 95.0)])
        self.assertEqual(
            next(item for item in result.constraints
                 if isinstance(item, QualificationConstraint)).min_batted_balls, 100)

    def test_unrecoverable_llm_contradiction_fails_closed(self):
        primary = LLMSemanticExtractor(FakeModelProvider(responses=[json.dumps({
            "constraints": [
                {"kind": "RANKING", "metric_key": "exit_velocity", "aggregation": "MAX",
                 "direction": "DESC", "limit": 5,
                 "evidence": {"text": "maximum exit velocity"}},
                {"kind": "RANKING", "metric_key": "exit_velocity", "aggregation": "AVG",
                 "direction": "DESC", "limit": 5,
                 "evidence": {"text": "average exit velocity"}},
            ]})]), "test-model")
        result = HybridSemanticParser(extractor=primary).parse(
            "top 5 by maximum exit velocity and average exit velocity")
        self.assertTrue(result.failed_closed)
        self.assertEqual(result.clarification_reason, "CONTRADICTORY_AGGREGATION")
        self.assertEqual(result.constraints, ())

    def test_ungrounded_llm_candidate_falls_back_to_deterministic(self):
        primary = LLMSemanticExtractor(FakeModelProvider(responses=[json.dumps({
            "constraints": [{"kind": "NUMERIC", "metric": "pitch_velocity", "operator": "GTE",
                             "value": 20.0, "unit": "mph",
                             "evidence": {"text": "20 BBE"}}]})]), "test-model")
        result = HybridSemanticParser(extractor=primary).parse(
            "top 5 by maximum exit velocity with >= 20 BBE in 2023")
        self.assertEqual(result.extractor, "deterministic")
        self.assertIn("EVIDENCE_METRIC_MISMATCH", result.fallback_reason)
        self.assertFalse(any(isinstance(item, NumericConstraint) for item in result.constraints))
        self.assertEqual(
            next(item for item in result.constraints
                 if isinstance(item, QualificationConstraint)).min_batted_balls, 20)


class NormalizerObservabilityTests(unittest.TestCase):
    def _normalizer(self):
        ids = lambda prefix: f"{prefix}-trace"  # noqa: E731
        dictionary = EntityDictionary()
        return SemanticNormalizer(
            RuleBasedObjectiveExtractor(id_factory=ids),
            EntityResolver(dictionary, id_factory=ids), dictionary, id_factory=ids,
            semantic_parser=HybridSemanticParser())

    def test_trace_records_extractor_version_and_constraint_summary(self):
        result = self._normalizer().normalize(
            "top 5 by maximum exit velocity on fastballs at least 95 mph, at least 100 BBE in 2023")
        notes = "\n".join(result.notes)
        self.assertIn("semantic parser: deterministic (hybrid-semantic-v1)", notes)
        self.assertIn("semantic constraints:", notes)
        self.assertIn("RANKING:ranking", notes)
        # The trace is bounded and never contains raw model text or credentials.
        self.assertNotIn("sk-", notes)

    def test_trace_records_ambiguity_kind(self):
        result = self._normalizer().normalize(
            "top 5 by exit velocity on high fastballs in 2023")
        self.assertIn("semantic ambiguities: location.upper_edge", "\n".join(result.notes))


if __name__ == "__main__":
    unittest.main()
