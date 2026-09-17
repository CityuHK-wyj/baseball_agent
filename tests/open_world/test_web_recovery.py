"""Web is a first-class recovery tool; evidence quality and provenance are preserved."""

import unittest

from app.models.contracts import ArtifactDescriptor, ArtifactRequirement
from app.models.entities import CanonicalEntity
from app.models.evidence import RawWebResult
from app.models.planning import AgentTask
from app.semantic.entity_recovery import EntityRecovery
from app.semantic.entity_resolver import EntityDictionary, EntityResolver
from app.semantic.evidence import RuleBasedEvidenceExtractor
from app.tools.web_evidence import WebEvidenceTool
from app.tools.web_fetch import StaticSearchBackend, WebResearchFetcher


def _dictionary() -> EntityDictionary:
    return EntityDictionary((
        CanonicalEntity(entity_key="MLBAM:660271", entity_type="PLAYER",
                        display_name="Shohei Ohtani", aliases=("Ohtani",)),
    ))


class EntityRecoveryTests(unittest.TestCase):
    def test_web_resolves_an_unknown_nickname_with_provenance(self):
        document = RawWebResult(result_id="web-1", url="https://example.test/x",
                                title="太鼓达人", text="太鼓达人 refers to Shohei Ohtani.")
        backend = StaticSearchBackend(default=(document,))
        recovery = EntityRecovery(_dictionary(), EntityResolver(_dictionary()),
                                  web_search=lambda mention: backend.search(mention))
        result = recovery.recover("太鼓达人")
        self.assertTrue(result.recovered)
        self.assertEqual(result.canonical.entity_key, "MLBAM:660271")
        self.assertEqual(result.source_url, "https://example.test/x")
        self.assertIn("Shohei Ohtani", result.evidence_text)

    def test_unknown_without_web_stays_unresolved_never_guessed(self):
        recovery = EntityRecovery(_dictionary(), EntityResolver(_dictionary()))
        result = recovery.recover("太鼓达人")
        self.assertFalse(result.recovered)
        self.assertIsNone(result.canonical)
        self.assertEqual(result.reason, "NO_LOCAL_MATCH")

    def test_two_web_candidates_remain_ambiguous(self):
        document = RawWebResult(
            result_id="web-1", url="https://example.test/x", title="nickname",
            text="The nickname could mean Shohei Ohtani or Ohtani the pitcher.")
        # Add a second entity so both are found.
        dictionary = EntityDictionary((
            CanonicalEntity(entity_key="MLBAM:1", entity_type="PLAYER", display_name="Shohei Ohtani"),
            CanonicalEntity(entity_key="MLBAM:2", entity_type="PLAYER", display_name="Ohtani"),
        ))
        recovery = EntityRecovery(dictionary, EntityResolver(dictionary),
                                  web_search=lambda mention: (document,))
        result = recovery.recover("太鼓达人")
        self.assertFalse(result.recovered)
        self.assertTrue(result.ambiguous)


class WebResearchFetcherTests(unittest.TestCase):
    def test_free_form_search_hints_drive_the_query(self):
        document = RawWebResult(result_id="web-1", url="https://example.test/a",
                                title="T", text="Claim text")
        backend = StaticSearchBackend(default=(document,))
        task = AgentTask(task_id="t1", objective_ref="o1", requirement_refs=("r1",),
                         description="research", task_type="WEB_RESEARCH",
                         objective="Determine the player nicknamed 太鼓达人",
                         search_hints=("太鼓达人 MLB", "太鼓达人 棒球"))
        raw = WebResearchFetcher(backend)(task)
        self.assertIn("Claim text", raw.text)
        self.assertEqual(raw.url, "https://example.test/a")


class WebEvidenceToolTests(unittest.TestCase):
    def _requirement(self) -> ArtifactRequirement:
        return ArtifactRequirement(
            requirement_id="r1", objective_ref="o1", description="injury evidence",
            descriptor=ArtifactDescriptor(artifact_type="EVIDENCE", data_keys=("injury_status",),
                                          granularity="event", population_scope="player"))

    def test_web_artifact_keeps_unstructured_text_and_provenance(self):
        document = RawWebResult(result_id="web-1", url="https://example.test/injury",
                                title="Injury report",
                                text="The player was placed on the injured list with a strain. "
                                     "He is out for 10 days.")
        tool = WebEvidenceTool(lambda task: document,
                               RuleBasedEvidenceExtractor(id_factory=lambda p: f"{p}-1"),
                               (self._requirement(),))
        task = AgentTask(task_id="t1", objective_ref="o1", requirement_refs=("r1",),
                         description="research injury", task_type="WEB_RESEARCH")
        result = tool.execute(task)
        self.assertEqual(result.status, "OK")
        artifact = result.artifact
        self.assertEqual(artifact.provenance.source_kind, "WEB")
        self.assertEqual(artifact.provenance.reference, "https://example.test/injury")
        self.assertTrue(artifact.text_content)


if __name__ == "__main__":
    unittest.main()
