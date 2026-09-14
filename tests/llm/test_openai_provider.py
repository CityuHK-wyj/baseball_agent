import os
import unittest
from unittest.mock import patch

from app.config import Settings
from app.llm.openai_provider import OpenAICompatibleProvider
from app.llm.provider import ProviderError


class OpenAIProviderTests(unittest.TestCase):
    def test_missing_credential_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            config = Settings()
        with self.assertRaises(ProviderError):
            OpenAICompatibleProvider(config).complete("prompt", model="m")

    def test_model_configuration_is_environment_driven(self):
        with patch.dict(os.environ, {"PLANNER_MODEL": "p-1", "JUDGE_MODEL": "j-1",
                                     "RESPONSE_MODEL": "r-1", "SEMANTIC_MODEL": "s-1"}):
            config = Settings()
        self.assertEqual((config.llm_planner_model, config.llm_judge_model,
                          config.llm_response_model, config.llm_semantic_model),
                         ("p-1", "j-1", "r-1", "s-1"))

    def test_credential_is_not_exposed_in_repr(self):
        api_key_field = "DEEPSEEK_" + "API_KEY"
        with patch.dict(os.environ, {api_key_field: "synthetic-key-value"}):
            config = Settings()
        self.assertNotIn("synthetic-key-value", repr(config))


if __name__ == "__main__":
    unittest.main()
