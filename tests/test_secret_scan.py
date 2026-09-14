import unittest
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from scripts.secret_scan import find_secrets


class SecretScanTests(unittest.TestCase):
    def test_provider_url_and_literal_password_are_redacted(self):
        samples = ["sk-" + "x" * 32, "postgresql://reader:" + "synthetic@localhost/db",
                   "password=" + repr("synthetic-value"), "POSTGRES_PASSWORD: " + "synthetic-value"]
        for sample in samples:
            with self.subTest(kind=sample.split(":")[0][:2]):
                findings = find_secrets(sample, "test.py")
                self.assertTrue(findings)
                self.assertNotIn(sample, repr(findings))
                self.assertIn("SECRET_REDACTED", repr(findings))

    def test_environment_references_and_empty_examples_are_safe(self):
        text = 'password=os.environ["POSTGRES_PASSWORD"]\nOPENAI_API_KEY=\nPOSTGRES_PASSWORD: ${POSTGRES_ADMIN_PASSWORD:?Required}'
        self.assertEqual(find_secrets(text, "config.py"), ())

    def test_auth_json_dictionary_and_notebook_credentials_are_detected(self):
        value = "synthetic-value"
        samples = [(json.dumps({"password": value}), "auth.json"),
                   ("config = " + repr({"password": value}), "config.py"),
                   ("password: " + value, "config.yaml"),
                   (json.dumps({"cells": [{"cell_type": "code", "source": ["password=" + repr(value)]}]}), "analysis.ipynb")]
        for content, name in samples:
            with self.subTest(name=name):
                self.assertTrue(find_secrets(content, name))

    def test_staged_scan_uses_index_even_when_worktree_is_cleaned(self):
        scanner = Path(__file__).resolve().parents[1] / "scripts" / "secret_scan.py"
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(["git", "init", "-q", directory], check=True)
            fixture = Path(directory) / "config.py"
            value = "sk-" + "x" * 32
            fixture.write_text("value=" + repr(value), encoding="utf-8")
            subprocess.run(["git", "-C", directory, "add", "config.py"], check=True)
            fixture.write_text("value=None", encoding="utf-8")
            result = subprocess.run([sys.executable, str(scanner), "--staged"], cwd=directory,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("SECRET_REDACTED", result.stdout)
            self.assertNotIn(value, result.stdout + result.stderr)
            subprocess.run(["git", "-C", directory, "add", "config.py"], check=True)
            clean = subprocess.run([sys.executable, str(scanner), "--staged"], cwd=directory,
                                   capture_output=True, text=True)
            self.assertEqual(clean.returncode, 0)
