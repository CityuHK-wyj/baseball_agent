"""CLI surface tests for the LLM-first runtime (no network)."""

import unittest

from app.cli import _map_choice, build_parser
from app.models.agent_runtime import PendingClarification


class _Conversation:
    def __init__(self, pending):
        self.pending_clarification = pending


class CliTests(unittest.TestCase):
    def test_parser_exposes_chat_and_llm_first_ask(self):
        parser = build_parser()
        args = parser.parse_args(["chat"])
        self.assertEqual(args.command, "chat")
        args = parser.parse_args(["ask", "q", "--trace", "--legacy"])
        self.assertTrue(args.trace)
        self.assertTrue(args.legacy)

    def test_numeric_clarification_answer_maps_to_option_text(self):
        conversation = _Conversation(PendingClarification(
            clarification_id="c", question="which?", options=("A", "B", "C")))
        self.assertEqual(_map_choice(conversation, "3"), "C")
        self.assertEqual(_map_choice(conversation, "2."), "B")
        self.assertEqual(_map_choice(conversation, "自定义"), "自定义")

    def test_free_form_answer_passes_through(self):
        conversation = _Conversation(PendingClarification(
            clarification_id="c", question="which?", options=("A", "B")))
        self.assertEqual(_map_choice(conversation, "按个人好球带上缘"), "按个人好球带上缘")


if __name__ == "__main__":
    unittest.main()
