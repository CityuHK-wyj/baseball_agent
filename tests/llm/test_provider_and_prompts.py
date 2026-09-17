import unittest

from app.llm.parsing import parse_json_object
from app.llm.prompts import PLANNER_PROMPT, PromptTemplate
from app.llm.provider import FakeModelProvider, ModelResponse, ProviderTimeout


class ProviderTests(unittest.TestCase):
    def test_fake_provider_returns_scripted_responses_and_records_calls(self):
        provider = FakeModelProvider(["first", "second"])
        self.assertEqual(provider.complete("p1", model="m").text, "first")
        self.assertEqual(provider.complete("p2", model="m").text, "second")
        self.assertEqual(provider.calls, [("m", "p1"), ("m", "p2")])

    def test_fake_provider_can_simulate_timeout(self):
        provider = FakeModelProvider(error=ProviderTimeout("deadline exceeded"))
        with self.assertRaises(ProviderTimeout):
            provider.complete("p", model="m")

    def test_model_response_is_a_plain_contract(self):
        response = ModelResponse(text="ok", model="m", usage={"prompt_tokens": 3})
        self.assertEqual(response.usage["prompt_tokens"], 3)


class PromptTests(unittest.TestCase):
    def test_render_substitutes_and_reports_missing_variables(self):
        template = PromptTemplate(prompt_id="t", template="hi {name}", required_variables=("name",))
        self.assertEqual(template.render(name="world"), "hi world")
        with self.assertRaises(KeyError):
            template.render()

    def test_template_identity_is_versioned(self):
        self.assertEqual(PLANNER_PROMPT.identity(), "planner.decide@v2")

    def test_parse_json_object_handles_fences_and_rejects_garbage(self):
        self.assertEqual(parse_json_object('```json\n{"a": 1}\n```'), {"a": 1})
        with self.assertRaises(ValueError):
            parse_json_object("not json")
        with self.assertRaises(ValueError):
            parse_json_object("")


if __name__ == "__main__":
    unittest.main()
