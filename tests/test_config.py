import os
import unittest
from unittest.mock import patch

from app.config import Settings


class SettingsTests(unittest.TestCase):
    def test_credentials_are_read_at_construction_and_hidden(self):
        llm_value, db_value = "synthetic-test-value", "synthetic-db-value"
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": llm_value, "POSTGRES_PASSWORD": db_value}):
            config = Settings()
        self.assertEqual(config.deepseek_api_key, "synthetic-test-value")
        self.assertEqual(config.postgres_password, "synthetic-db-value")
        self.assertNotIn("synthetic-test-value", repr(config))
        self.assertNotIn("synthetic-db-value", repr(config))

    def test_no_credentials_or_admin_default(self):
        with patch.dict(os.environ, {}, clear=True):
            config = Settings()
        self.assertIsNone(config.deepseek_api_key)
        self.assertIsNone(config.postgres_password)
        self.assertEqual(config.postgres_user, "baseball_readonly")

    def test_runtime_model_defaults_to_deepseek_flash(self):
        with patch.dict(os.environ, {}, clear=True):
            config = Settings()
        self.assertEqual(config.runtime_model, "deepseek-flash")
        roles = config.runtime_llm_roles()
        for role in ("semantic", "planner", "response", "judge"):
            self.assertEqual(roles[role]["model"], "deepseek-flash")
            self.assertGreater(roles[role]["max_tokens"], 0)
        self.assertEqual(config.llm_reasoning_effort, "none")
        self.assertTrue(config.llm_stream)

    def test_role_overrides_and_shared_runtime_model(self):
        with patch.dict(os.environ, {"RUNTIME_MODEL": "shared-model"}, clear=True):
            config = Settings()
        self.assertEqual(config.llm_semantic_model, "shared-model")
        self.assertEqual(config.llm_planner_model, "shared-model")
        with patch.dict(os.environ, {"RUNTIME_MODEL": "shared-model",
                                     "PLANNER_MODEL": "planner-only"}, clear=True):
            config = Settings()
        self.assertEqual(config.llm_planner_model, "planner-only")
        self.assertEqual(config.llm_semantic_model, "shared-model")
