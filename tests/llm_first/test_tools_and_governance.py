"""Tool and governance tests for the LLM-first runtime (no network)."""

import unittest

from app.knowledge.candidates import CandidateKnowledge, CandidateKnowledgeStore
from app.tools.batting import (BattingLine, BattingStatsTool, batting_evidence,
                               select_players, select_team)
from app.tools.web_research import WebResearchTool, WebSearchResult


class _FakeBackend:
    def __init__(self, results):
        self._results = results

    def search(self, query, *, max_results=6):
        return tuple(self._results)


class _FakeReader:
    def __init__(self, text="page text"):
        self._text = text
        self.read_urls = []

    def read(self, url):
        self.read_urls.append(url)
        return self._text


class WebResearchToolTests(unittest.TestCase):
    def test_research_returns_unstructured_evidenced_findings(self):
        backend = _FakeBackend([WebSearchResult(title="T1", url="https://example.test/a",
                                                snippet="snip1"),
                                WebSearchResult(title="T2", url="https://example.test/b",
                                                snippet="snip2")])
        reader = _FakeReader("fetched page body")
        tool = WebResearchTool(backends=(backend,), reader=reader, fetch_pages=1)
        evidence = tool.research("太鼓达人 棒球")
        self.assertEqual(len(evidence), 2)
        self.assertTrue(all(item.kind == "WEB" for item in evidence))
        self.assertEqual(evidence[0].reference, "https://example.test/a")
        self.assertIn("fetched page body", evidence[0].text)
        self.assertEqual(reader.read_urls, ["https://example.test/a"])

    def test_research_is_not_sql_shaped(self):
        backend = _FakeBackend([WebSearchResult(title="T", url="https://example.test/a",
                                                snippet="s")])
        tool = WebResearchTool(backends=(backend,), reader=_FakeReader(), fetch_pages=0)
        item = tool.research("q")[0]
        self.assertNotIn("columns", item.data)
        self.assertEqual(item.data["query"], "q")


class BattingStatsTests(unittest.TestCase):
    def _lines(self):
        return (
            BattingLine("Shohei Ohtani", "660271", "Los Angeles", 596, 30, 94, 184, 0.277,
                        0.38, 0.522, 0.902),
            BattingLine("Aaron Judge", "592450", "New York", 283, 18, 43, 100, 0.243,
                        0.363, 0.515, 0.878),
            BattingLine("Mookie Betts", "605141", "Los Angeles", 500, 19, 60, 90, 0.28,
                        0.36, 0.48, 0.84),
        )

    def test_select_players_matches_loosely(self):
        selected = select_players(self._lines(), ("ohtani", "Judge"))
        self.assertEqual({line.name for line in selected}, {"Shohei Ohtani", "Aaron Judge"})

    def test_select_team_flags_shared_city(self):
        rows = select_team(self._lines(), "Los Angeles Dodgers")
        self.assertEqual({line.name for line in rows}, {"Shohei Ohtani", "Mookie Betts"})
        evidence = batting_evidence(rows, title="t", source_label="bref",
                                    caveats=("shared city",))
        self.assertIn("shared city", evidence.summary)

    def test_evidence_contains_real_rate_stats(self):
        evidence = batting_evidence(self._lines(), title="t", source_label="bref",
                                    metric="OPS", limit=2)
        self.assertEqual(evidence.data["rows"][0]["name"], "Shohei Ohtani")
        self.assertEqual(evidence.data["rows"][0]["OPS"], 0.902)
        self.assertIn("BB%", evidence.data["rows"][0])

    def test_tool_uses_injected_client(self):
        class _Client:
            def season(self, year):
                return self_lines

        self_lines = self._lines()
        tool = BattingStatsTool(client=_Client())
        evidence = tool.season_evidence(2025, names=("Ohtani",), metric="OPS")
        self.assertEqual(evidence.kind, "BATTING_STATS")
        self.assertEqual(evidence.data["rows"][0]["name"], "Shohei Ohtani")


class CandidateKnowledgeGovernanceTests(unittest.TestCase):
    def test_runtime_submission_is_pending_and_requires_review(self):
        store = CandidateKnowledgeStore(":memory:")
        candidate = CandidateKnowledge(
            candidate_id="c1", surface="太鼓达人", meaning="2017 Astros sign-stealing slang",
            evidence=("2017年太空人擊敗道奇...",), provenance=("https://example.test/a",))
        store.submit(candidate)
        self.assertEqual(store.list()[0].status, "CANDIDATE")
        approved = store.review("c1", approve=True)
        self.assertEqual(approved.status, "APPROVED")
        rejected = store.review("c1", approve=False)
        self.assertEqual(rejected.status, "REJECTED")


if __name__ == "__main__":
    unittest.main()
