"""CLI and live-refresh behaviour, exercised without touching the network."""

import io
import json
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path

from app.cli import main
from app.knowledge.fetch import FetchError
from app.knowledge import refresh as refresh_module
from app.knowledge.loader import seed_store
from app.knowledge.service import KnowledgeBase
from app.knowledge.store import SqliteKnowledgeStore

ROOT = Path(__file__).resolve().parents[2]


def fake_team(key: str, name: str, venue: str, division: str = "American League West",
              league: str = "American League") -> dict:
    return {
        "id": 900 + abs(hash(key)) % 90, "name": name, "abbreviation": key,
        "teamName": name.split()[-1], "shortName": name, "locationName": name.split()[0],
        "teamCode": key.lower(), "firstYearOfPlay": "1900",
        "league": {"name": league}, "division": {"name": division},
        "venue": {"id": 1, "name": venue},
    }


def all_fake_teams(store) -> list[dict]:
    keys = sorted(item.canonical_key for item in store.list_items(knowledge_type="TEAM"))
    return [fake_team(key, f"Team {key}", f"{key} Park") for key in keys]


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.store = SqliteKnowledgeStore(":memory:")
        seed_store(self.store, ROOT / "knowledge" / "sources", ROOT / "knowledge" / "seed")
        self.kb = KnowledgeBase(self.store)

    def tearDown(self):
        self.store.close()

    def test_reference_refresh_updates_live_fields_and_keeps_curated_identity(self):
        teams = [team for team in all_fake_teams(self.store) if team["abbreviation"] != "LAD"]
        teams.append(fake_team("LAD", "Los Angeles Dodgers", "New Sponsor Park",
                               division="National League West", league="National League"))
        diff = refresh_module.refresh_reference(self.store, teams=tuple(teams), as_of=date(2026, 9, 15))
        self.assertIn("TEAM:LAD", diff.updated)
        lad = self.store.get_item("TEAM:LAD")
        self.assertEqual(lad.structured_payload["venue"], "New Sponsor Park")
        self.assertEqual(lad.structured_payload["zh_name"], "洛杉矶道奇")  # curated field preserved
        self.assertEqual(lad.last_verified_at.date(), date(2026, 9, 15))
        self.assertTrue(lad.structured_payload["franchise_lineage"])

    def test_reference_refresh_supersedes_a_franchise_key_that_disappears(self):
        teams = [team for team in all_fake_teams(self.store) if team["abbreviation"] != "ATH"]
        teams.append(fake_team("OAK", "Athletics", "Sutter Health Park"))
        self.assertEqual(len(teams), 30)
        refresh_module.refresh_reference(self.store, teams=tuple(teams), as_of=date(2026, 9, 15))
        self.assertEqual(self.store.get_item("TEAM:ATH").status, "SUPERSEDED")
        self.assertEqual(self.store.get_item("TEAM:OAK").status, "ACTIVE")

    def test_reference_refresh_rejects_a_wrong_team_count(self):
        with self.assertRaises(FetchError):
            refresh_module.refresh_reference(self.store, teams=(fake_team("LAD", "Dodgers", "X"),))

    def test_rules_refresh_reports_fetch_failure_without_corrupting_the_store(self):
        original = refresh_module.fetch

        def boom(*args, **kwargs):
            raise FetchError("network down")

        refresh_module.fetch = boom
        try:
            with self.assertRaises(FetchError):
                refresh_module.refresh_rules(self.store, years=(2026,))
        finally:
            refresh_module.fetch = original
        self.assertEqual(self.store.get_item("RULEBOOK:OBR_2026").status, "ACTIVE")

    def test_refresh_domain_reloads_seed_for_non_live_domains(self):
        diff = refresh_module.refresh_domain(self.store, "glossary",
                                             seed_dir=ROOT / "knowledge" / "seed",
                                             source_dir=ROOT / "knowledge" / "sources")
        self.assertEqual(diff.domain, "glossary")
        self.assertIn("seed pack", diff.message)


class CliTests(unittest.TestCase):
    def test_knowledge_validate_command_succeeds(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(["knowledge", "validate"])
        self.assertEqual(code, 0, buffer.getvalue())
        self.assertIn("reference", buffer.getvalue())
        self.assertIn("rules", buffer.getvalue())

    def test_knowledge_parser_exposes_the_required_subcommands(self):
        from app.cli import build_parser

        parser = build_parser()
        for argv in (["knowledge", "status"], ["knowledge", "sources"], ["knowledge", "search", "balk"],
                     ["knowledge", "show", "TEAM:LAD"], ["knowledge", "refresh", "rules"],
                     ["knowledge", "validate"]):
            with self.subTest(argv=argv):
                args = parser.parse_args(argv)
                self.assertTrue(callable(args.func))


if __name__ == "__main__":
    unittest.main()
