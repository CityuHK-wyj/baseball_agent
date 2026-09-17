"""Real-user regression corpus and open-world behavior.

These are the exact queries observed in v0.1 dogfooding that failed because natural
language was forced into a closed typed representation too early. The invariant under
test is:

    understand goal -> plan -> gather -> re-plan -> answer / limited / clarify

never:

    unknown enum -> uncaught exception -> FAILED
"""

import tempfile
import unittest
from datetime import date
from pathlib import Path

from app.models.entities import CanonicalEntity
from app.models.evidence import RawWebResult
from app.pipeline import AnalysisPipeline
from app.semantic.entity_recovery import EntityRecovery
from app.semantic.entity_resolver import EntityDictionary, EntityResolver
from app.semantic.normalizer import SemanticNormalizer
from app.semantic.objective_extractor import RuleBasedObjectiveExtractor
from app.tools.web_fetch import StaticSearchBackend


def _pipeline(root: Path) -> AnalysisPipeline:
    return AnalysisPipeline.default(runtime_dir=root, demo=True,
                                    today=lambda: date(2026, 9, 16))


def _dictionary() -> EntityDictionary:
    return EntityDictionary((
        CanonicalEntity(entity_key="MLBAM:660271", entity_type="PLAYER",
                        display_name="Shohei Ohtani", aliases=("Ohtani",)),
        CanonicalEntity(entity_key="MLBAM:592450", entity_type="PLAYER",
                        display_name="Aaron Judge", aliases=("Judge",)),
        CanonicalEntity(entity_key="MLBAM:518692", entity_type="PLAYER",
                        display_name="Freddie Freeman", aliases=("Freeman",)),
        CanonicalEntity(entity_key="TEAM:LAD", entity_type="TEAM",
                        display_name="Los Angeles Dodgers", aliases=("Dodgers", "道奇")),
    ))


def _normalizer(entity_recovery=None) -> SemanticNormalizer:
    dictionary = _dictionary()
    ids = lambda prefix: f"{prefix}-1"  # noqa: E731
    resolver = EntityResolver(dictionary, id_factory=ids)
    return SemanticNormalizer(RuleBasedObjectiveExtractor(id_factory=ids), resolver,
                              dictionary, id_factory=ids,
                              today=lambda: date(2026, 9, 16), entity_recovery=entity_recovery)


class QueryARegressionTests(unittest.TestCase):
    """太鼓达人今年战绩如何？"""

    def test_unknown_nickname_is_not_silently_dropped(self):
        result = _normalizer().normalize("太鼓达人今年战绩如何？")
        self.assertIn("太鼓达人", result.understanding.candidate_entity_mentions)
        self.assertIn("太鼓达人", result.understanding.unresolved_concepts)
        # Without a web recovery path the honest behavior is a human-readable entity
        # clarification, never a silent drop and never an opaque UUID.
        self.assertTrue(result.needs_clarification)
        request = result.clarifications[0]
        self.assertEqual(request.kind, "ENTITY")
        self.assertIn("太鼓达人", request.question)
        self.assertNotIn("requirement-", request.question)

    def test_web_resolution_recovers_the_nickname_when_configured(self):
        document = RawWebResult(
            result_id="web-1", url="https://example.test/taiko",
            title="太鼓达人 is Shohei Ohtani's community nickname",
            text="The MLB player commonly referred to as 太鼓达人 is Shohei Ohtani.")
        backend = StaticSearchBackend(default=(document,))
        dictionary = _dictionary()
        recovery = EntityRecovery(dictionary, EntityResolver(dictionary),
                                  web_search=lambda mention: backend.search(mention))
        result = _normalizer(entity_recovery=recovery).normalize("太鼓达人今年战绩如何？")
        self.assertFalse(result.needs_clarification)
        self.assertTrue(any(entity.identifier == "660271"
                            for objective in result.objectives for entity in objective.entities))


class QueryBRegressionTests(unittest.TestCase):
    """2023和2025，Freddie Freeman面对高区快速球的EV有什么变化？"""

    def test_two_year_comparison_never_raises_and_keeps_the_entity_and_metric(self):
        result = _normalizer().normalize(
            "2023和2025，Freddie Freeman面对高区快速球的EV有什么变化？")
        self.assertEqual(len(result.objectives), 2)
        windows = sorted(next(c.values for c in obj.constraints if c.key == "date_range")
                         for obj in result.objectives)
        self.assertEqual(windows, [("2023-01-01", "2023-12-31"), ("2025-01-01", "2025-12-31")])
        self.assertTrue(all(any(entity.identifier == "518692" for entity in obj.entities)
                            for obj in result.objectives))
        strategies = result.understanding.analysis_strategy
        self.assertTrue(strategies)  # "有什么变化" is a comparison intent


class QueryCAndDTests(unittest.TestCase):
    """Vague analytical goals must not require a metric clarification."""

    def test_dodgers_hitters_skill_produces_an_analysis_strategy(self):
        result = _normalizer().normalize("今年道奇打者里谁最擅长处理高区快速球？")
        self.assertFalse(result.needs_clarification)
        self.assertIn("bounded profile", result.understanding.analysis_strategy)

    def test_recent_two_player_comparison_uses_a_single_window_and_strategy(self):
        result = _normalizer().normalize("最近30天Ohtani和Judge谁打得更好？")
        self.assertFalse(result.needs_clarification)
        self.assertEqual(len(result.objectives), 1)
        window = next(c.values for c in result.objectives[0].constraints if c.key == "date_range")
        self.assertEqual(window, ("2026-08-18", "2026-09-16"))
        identifiers = {entity.identifier for entity in result.objectives[0].entities}
        self.assertEqual(identifiers, {"660271", "592450"})
        self.assertIn("balanced profile", result.understanding.analysis_strategy)


class PipelineCorpusTests(unittest.TestCase):
    """The five real-user queries behave meaningfully end to end (offline synthetic)."""

    def test_all_five_real_user_queries_never_raise(self):
        with tempfile.TemporaryDirectory() as directory:
            subject = _pipeline(Path(directory))
            self.addCleanup(subject.close)
            for query in (
                "太鼓达人今年战绩如何？",
                "2023和2025，Freddie Freeman面对高区快速球的EV有什么变化？",
                "今年道奇打者里谁最擅长处理高区快速球？",
                "最近30天Ohtani和Judge谁打得更好？",
                "DFA是什么意思？",
            ):
                with self.subTest(query=query):
                    result = subject.analyze(query)
                    self.assertIsInstance(result.objectives, tuple)

    def test_query_a_clarifies_with_a_human_readable_question(self):
        with tempfile.TemporaryDirectory() as directory:
            subject = _pipeline(Path(directory))
            self.addCleanup(subject.close)
            result = subject.analyze("太鼓达人今年战绩如何？")
            self.assertTrue(result.needs_clarification)
            question = result.clarifications[0].question
            self.assertIn("太鼓达人", question)
            self.assertNotIn("requirement-", question)

    def test_query_e_definition_still_uses_shared_knowledge(self):
        with tempfile.TemporaryDirectory() as directory:
            subject = _pipeline(Path(directory))
            self.addCleanup(subject.close)
            result = subject.analyze("DFA是什么意思？")
            self.assertEqual(result.objective_statuses, ("COMPLETE",))
            self.assertIn("40-man", result.responses[0])


if __name__ == "__main__":
    unittest.main()
