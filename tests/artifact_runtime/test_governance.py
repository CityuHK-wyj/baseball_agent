"""Shared Knowledge governance: candidates require administrator approval."""

import unittest

from app.knowledge.candidates import CandidateKnowledge, CandidateKnowledgeStore, CandidateScope
from app.knowledge.governance import GovernanceError, KnowledgeGovernance
from app.models.knowledge import KnowledgeItem

from tests.artifact_runtime.fakes import knowledge_base


def _item(surface="太鼓达人", meaning="Astros sign-stealing slang", **changes):
    fields = dict(knowledge_id=f"TERM:{surface}", canonical_key=surface,
                  knowledge_type="TERM", title=surface, aliases=(surface,),
                  summary=meaning, source_authority="COMMUNITY", status="ACTIVE")
    fields.update(changes)
    return KnowledgeItem(**fields)


class CandidateGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.candidates = CandidateKnowledgeStore(":memory:")
        self.knowledge = knowledge_base((_item(),))
        self.governance = KnowledgeGovernance(self.candidates, self.knowledge)

    def _candidate(self, **changes):
        fields = dict(candidate_id="c1", proposed_type="COMMUNITY_REFERENCE",
                      surface="太鼓达人", meaning="A community nickname for a 2017 event",
                      language="zh", evidence=("quoted source",),
                      provenance=("https://example.test/a",))
        fields.update(changes)
        candidate = CandidateKnowledge(**fields)
        self.candidates.submit(candidate)
        return candidate

    def test_a_candidate_is_not_authoritative_retrieval(self):
        self._candidate()
        matches = self.knowledge.search("太鼓达人")
        # Only the pre-existing ACTIVE item is returnable; the candidate alone is not.
        self.assertTrue(all(match.item.knowledge_id == "TERM:太鼓达人" for match in matches))

    def test_candidate_stays_pending_until_approved(self):
        self._candidate()
        self.assertEqual(self.candidates.list()[0].status, "CANDIDATE")

    def test_approval_is_the_only_path_to_active_knowledge(self):
        self._candidate()
        item = self.governance.approve("c1", supersede=True, note="reviewed")
        self.assertEqual(item.status, "ACTIVE")
        self.assertEqual(self.candidates.get("c1").status, "ACTIVE")

    def test_conflict_is_surfaced_and_not_silently_overwritten(self):
        self._candidate()  # same surface, different meaning -> conflict
        conflicts = self.governance.conflicts(self.candidates.get("c1"))
        self.assertTrue(conflicts)
        with self.assertRaises(GovernanceError):
            self.governance.approve("c1")

    def test_supersede_explicitly_replaces_conflicting_knowledge(self):
        self._candidate()
        self.governance.approve("c1", supersede=True)
        existing = self.knowledge.store.get_item("TERM:太鼓达人")
        self.assertEqual(existing.status, "SUPERSEDED")

    def test_reject_creates_no_active_knowledge(self):
        self._candidate()
        rejected = self.governance.reject("c1", reason="insufficient sourcing")
        self.assertEqual(rejected.status, "REJECTED")
        self.assertIsNone(self.knowledge.store.get_item("CAND-c1"))

    def test_edit_approve_applies_reviewer_edits(self):
        self._candidate(scope=CandidateScope(community="community-a", authority="COMMUNITY"))
        item = self.governance.edit_approve(
            "c1", {"meaning": "reviewer-corrected meaning"}, supersede=True)
        self.assertEqual(item.summary, "reviewer-corrected meaning")
        self.assertIn("community-a", item.tags)

    def test_explicit_knowledge_categories_are_preserved(self):
        self._candidate(candidate_id="alias", proposed_type="ENTITY_ALIAS",
                        surface="The Judge", meaning="Aaron Judge",
                        provenance=("https://example.test/b",))
        self.assertEqual(self.candidates.get("alias").effective_category, "ENTITY_ALIAS")

    def test_runtime_candidate_sink_never_writes_active_knowledge(self):
        from app.artifact_runtime.engine import ArtifactRuntime
        from app.artifact_runtime.planner import DeterministicPlanner
        from app.artifact_runtime.response import DeterministicResponseComposer
        from app.artifact_runtime.tool_base import ToolRegistry
        from app.artifact_runtime.tools_evidence import KnowledgeTool
        from app.models.artifact_runtime import SemanticBrief
        from tests.artifact_runtime.fakes import ScriptedInterpreter, entity_lookup

        brief = SemanticBrief(brief_id="b", goal_statement="who is 太鼓达人?",
                              unresolved=("太鼓达人",), research_queries=("太鼓达人",))

        def sink(payload: dict) -> None:
            self.candidates.submit(CandidateKnowledge(
                candidate_id="cand-runtime", proposed_type="CONTEXT_REFERENCE",
                surface=payload["surface"], context=payload.get("context", ""),
                discovered_from_query=payload.get("discovered_from_query", "")))

        runtime = ArtifactRuntime(
            interpreter=ScriptedInterpreter(brief), planner=DeterministicPlanner(),
            registry=ToolRegistry((KnowledgeTool(),)),
            composer=DeterministicResponseComposer(), knowledge=self.knowledge,
            entity_lookup=entity_lookup(), candidate_sink=sink)
        runtime.send_message(runtime.start_conversation(), "who is 太鼓达人?")
        candidate = self.candidates.get("cand-runtime")
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.status, "CANDIDATE")
        self.assertIsNone(self.knowledge.store.get_item("CAND-cand-runtime"))


if __name__ == "__main__":
    unittest.main()
