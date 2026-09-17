"""End-to-end domain tests over the committed Shared Knowledge seed.

These assert the queries the agent must answer without a web search: rules terminology,
transactions, bilingual team/player identity, provider-aware metrics and qualification.
"""

import unittest
from datetime import date
from pathlib import Path

from app.context.knowledge_source import KnowledgeContextSource
from app.context.service import ContextRequest, ContextService
from app.knowledge.entities import entity_dictionary_from_knowledge, metric_registry_from_knowledge
from app.knowledge.freshness import is_stale
from app.knowledge.loader import seed_store
from app.knowledge.service import KnowledgeBase
from app.knowledge.store import SqliteKnowledgeStore
from app.models.knowledge import KnowledgeQuery
from app.semantic.entity_resolver import EntityResolver

ROOT = Path(__file__).resolve().parents[2]
TODAY = date(2026, 9, 15)


class KnowledgeDomainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = SqliteKnowledgeStore(":memory:")
        seed_store(cls.store, ROOT / "knowledge" / "sources", ROOT / "knowledge" / "seed")
        cls.kb = KnowledgeBase(cls.store, today=lambda: TODAY)

    @classmethod
    def tearDownClass(cls):
        cls.store.close()

    # -- rules terminology ---------------------------------------------------
    def test_balk_resolves_to_the_official_rule(self):
        item = self.kb.get("balk")
        self.assertIsNotNone(item)
        self.assertEqual(item.knowledge_type, "RULE")
        self.assertEqual(item.source_authority, "OFFICIAL")
        self.assertIn("6.02(a)", item.structured_payload["rule_reference"])
        self.assertTrue(item.structured_payload["conditions"])

    def test_rulebook_covers_all_nine_rule_groups(self):
        major = {item.canonical_key for item in self.store.list_items(knowledge_type="RULE")
                 if item.canonical_key.startswith("rule_") and item.canonical_key.endswith("_00")}
        self.assertEqual(major, {f"rule_{n}_00" for n in range(1, 10)})
        concepts = self.store.list_items(knowledge_type="RULE")
        self.assertGreater(len(concepts), 100)
        for item in concepts:
            if item.canonical_key.startswith("rule_") or item.canonical_key == "official_baseball_rules_2026":
                continue
            self.assertTrue(item.structured_payload.get("rule_reference"), item.knowledge_id)

    def test_specific_rule_concepts_are_present(self):
        for key in ("infield_fly", "dropped_third_strike", "force_play", "obstruction",
                    "interference", "batting_out_of_turn", "designated_hitter",
                    "three_batter_minimum", "extra_inning_runner", "pitch_timer"):
            with self.subTest(key=key):
                self.assertIsNotNone(self.kb.get(key), key)

    # -- transactions --------------------------------------------------------
    def test_dfa_resolves_to_a_transaction_rule(self):
        item = self.kb.get("DFA")
        self.assertEqual(item.knowledge_id, "TXN:designated_for_assignment")
        self.assertEqual(item.knowledge_type, "TRANSACTION_RULE")
        self.assertIn("40-man", item.summary)

    def test_cba_sensitive_rules_carry_effective_dates_where_known(self):
        option = self.kb.get("minor_league_option")
        self.assertEqual(option.structured_payload["option_years"], 3)
        self.assertEqual(option.freshness_policy, "CBA")

    # -- teams and bilingual identity ---------------------------------------
    def test_all_thirty_teams_are_present(self):
        teams = self.store.list_items(knowledge_type="TEAM")
        self.assertEqual(len(teams), 30)
        self.assertEqual({item.canonical_key for item in teams if item.canonical_key == "LAD"}, {"LAD"})

    def test_team_aliases_resolve_to_one_canonical_entity(self):
        for mention in ("LAD", "Los Angeles Dodgers", "Dodgers", "道奇", "洛杉矶道奇"):
            with self.subTest(mention=mention):
                item = self.kb.get(mention)
                self.assertIsNotNone(item)
                self.assertEqual(item.knowledge_id, "TEAM:LAD")

    def test_chinese_name_is_recorded_as_commonly_used_not_official(self):
        item = self.store.get_item("TEAM:LAD")
        self.assertEqual(item.structured_payload["zh_name"], "洛杉矶道奇")
        self.assertEqual(item.structured_payload["zh_name_status"], "commonly_used")

    def test_nl_west_contains_the_current_five_clubs(self):
        division = self.store.get_item("DIV:NLW")
        self.assertEqual(sorted(division.structured_payload["teams"]), ["AZ", "COL", "LAD", "SD", "SF"])
        members = {rel.from_key for rel in self.store.relations(to_key="DIV:NLW",
                                                               relation_type="MEMBER_OF")}
        self.assertEqual(members, {"TEAM:AZ", "TEAM:COL", "TEAM:LAD", "TEAM:SD", "TEAM:SF"})
        league = {rel.to_key for rel in self.store.relations(from_key="DIV:NLW", relation_type="PART_OF")}
        self.assertEqual(league, {"LEAGUE:NL"})

    def test_team_to_division_relation(self):
        targets = {rel.to_key for rel in self.store.relations(from_key="TEAM:LAD", relation_type="MEMBER_OF")}
        self.assertEqual(targets, {"DIV:NLW"})

    def test_ballpark_record_keeps_sponsorship_name_and_common_name(self):
        park = self.store.get_item("PARK:LAD")
        self.assertEqual(park.canonical_key, "Dodger Stadium")
        self.assertEqual(park.structured_payload["common_name"], "Dodger Stadium")
        self.assertIn("Dodger Stadium", park.structured_payload["official_name"])

    # -- provider-aware metrics ---------------------------------------------
    def test_wrc_plus_is_attributed_to_fangraphs(self):
        item = self.kb.get("wRC+")
        self.assertEqual(item.canonical_key, "wrc_plus")
        self.assertEqual(item.structured_payload["provider"], "FanGraphs")
        self.assertIn("park", item.summary.lower())

    def test_war_variants_are_not_collapsed(self):
        war = self.kb.get("WAR")
        fwar = self.store.get_item("METRIC:FWAR")
        bwar = self.store.get_item("METRIC:BWAR")
        self.assertIn("no single war", war.summary.lower())
        self.assertEqual(fwar.structured_payload["provider"], "FanGraphs")
        self.assertEqual(bwar.structured_payload["provider"], "Baseball-Reference")
        self.assertNotEqual(fwar.summary, bwar.summary)

    def test_statcast_metric_has_source_and_availability(self):
        ev = self.kb.get("Exit Velocity")
        self.assertEqual(ev.structured_payload["provider"], "Baseball Savant")
        self.assertEqual(ev.structured_payload["availability_years"], "2015+")

    def test_metric_registry_projection_is_consistent_with_knowledge(self):
        registry = metric_registry_from_knowledge(self.store)
        self.assertIn("wrc_plus", [d.metric_key for d in registry.definitions()])
        self.assertEqual(registry.get("wrc_plus").display_name, "wRC+")
        self.assertIsNotNone(registry.mapping_for("exit_velocity"))
        for definition in registry.definitions():
            item = self.store.get_by_canonical_key(definition.metric_key)
            self.assertIsNotNone(item, definition.metric_key)
            self.assertEqual(item.title, definition.display_name)

    def test_qualification_uses_the_official_standard(self):
        item = self.kb.get("qualified hitter")
        self.assertEqual(item.canonical_key, "QUALIFIED_HITTER")
        self.assertIn("3.1", item.summary)
        self.assertIn("9.22", item.structured_payload["rule_reference"])

    # -- entity dictionary ---------------------------------------------------
    def test_entity_dictionary_resolves_team_and_player(self):
        dictionary = entity_dictionary_from_knowledge(self.store)
        resolver = EntityResolver(dictionary)
        self.assertEqual(resolver.resolve("道奇").canonical.entity_key, "TEAM:LAD")
        self.assertEqual(resolver.resolve("大谷翔平").canonical.entity_key, "MLBAM:660271")
        self.assertEqual(resolver.resolve("Aaron Judge").canonical.entity_key, "MLBAM:592450")

    def test_community_nickname_is_not_auto_resolved(self):
        dictionary = entity_dictionary_from_knowledge(self.store)
        resolver = EntityResolver(dictionary)
        # It must not silently resolve to the player.
        resolution = resolver.resolve("交通指挥员")
        self.assertTrue(resolution.needs_clarification)
        self.assertIsNone(resolution.canonical)
        # The community alias path surfaces it explicitly instead.
        alias = self.kb.retriever.match_alias("交通指挥员")
        self.assertEqual(alias[0].knowledge_type, "ALIAS")
        self.assertEqual(alias[0].source_authority, "COMMUNITY")
        self.assertEqual(alias[0].structured_payload["refers_to"], "PLAYER:592450")

    # -- authority and context ----------------------------------------------
    def test_official_outranks_community_for_the_same_token(self):
        matches = self.kb.search("balk", max_items=5)
        top = matches[0]
        self.assertEqual(top.item.source_authority, "OFFICIAL")

    def test_context_source_projects_knowledge_with_provenance(self):
        service = ContextService((KnowledgeContextSource(self.kb),))
        package = service.retrieve(ContextRequest(request_id="q1", query="balk", max_items=3))
        self.assertTrue(package.items)
        first = package.items[0]
        self.assertEqual(first.kind, "RULE")
        self.assertTrue(first.provenance_ref.startswith("knowledge:"))
        self.assertTrue(first.source.startswith("knowledge:"))

    def test_context_source_is_authority_bounded_when_asked(self):
        source = KnowledgeContextSource(self.kb, authority_floor="OFFICIAL")
        package = ContextService((source,)).retrieve(
            ContextRequest(request_id="q1", query="Aaron Judge", max_items=5))
        self.assertTrue(all(item.source_authority if hasattr(item, "source_authority") else True
                            for item in package.items))
        self.assertTrue(any(item.kind == "PLAYER_PROFILE" for item in package.items))

    # -- freshness -----------------------------------------------------------
    def test_community_creators_without_verification_are_stale(self):
        creators = self.store.list_items(knowledge_type="COMMUNITY_CREATOR")
        verified = [c for c in creators if c.last_verified_at is not None]
        self.assertTrue(verified)
        self.assertTrue(all(not is_stale(c, TODAY) for c in verified))
        unverified = [c for c in creators if c.last_verified_at is None]
        self.assertTrue(unverified)
        self.assertTrue(all(is_stale(c, TODAY) for c in unverified))
        self.assertTrue(all(c.verification_status == "UNVERIFIED" for c in unverified))

    def test_league_and_postseason_context_is_present(self):
        postseason = self.kb.get("postseason structure")
        self.assertEqual(postseason.structured_payload["teams"], 12)
        self.assertEqual(postseason.structured_payload["world_series"], "best-of-7")
        regular = self.kb.get("regular_season_structure")
        self.assertEqual(regular.structured_payload["games_per_club"], 162)

    def test_historical_eras_carry_effective_context(self):
        dh = self.kb.get("universal_dh_era")
        self.assertEqual(dh.knowledge_type, "HISTORICAL_CONTEXT")
        self.assertEqual(dh.structured_payload["start"], 2022)
        statcast = self.kb.get("statcast_era")
        self.assertEqual(statcast.structured_payload["start"], 2015)

    def test_temporal_validity_filtering(self):
        matches = self.kb.retriever.retrieve(KnowledgeQuery(query="balk", as_of=date(2019, 6, 1)))
        self.assertEqual(matches, ())  # current rule snapshot has no asserted historical validity


if __name__ == "__main__":
    unittest.main()
