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
