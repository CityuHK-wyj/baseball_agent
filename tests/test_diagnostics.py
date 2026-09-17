"""Tests for the non-destructive doctor/smoke preflight."""

import os
import unittest
from unittest.mock import patch

from app.config import Settings
from app.diagnostics import DoctorReport, run_doctor
from app.llm.provider import FakeModelProvider, ProviderError


def _settings(*, llm="test-llm-value", db="test-db-value", user="baseball_readonly"):
    env = {"POSTGRES_USER": user}
    if llm is not None:
        env["DEEPSEEK_API_KEY"] = llm
    if db is not None:
        env["POSTGRES_PASSWORD"] = db
    with patch.dict(os.environ, env, clear=True):
        return Settings()


class DoctorTests(unittest.TestCase):
    def test_no_credential_warns_but_does_not_fail(self):
        report = run_doctor(_settings(llm=None, db=None),
                            check_llm=False, parquet_sample=lambda path: 5)
        statuses = {item.name: item.status for item in report.checks}
        self.assertEqual(statuses["config.llm"], "WARN")
        self.assertEqual(statuses["postgres.readonly"], "WARN")
        self.assertFalse(report.failed)

    def test_wrong_runtime_user_fails_closed(self):
        report = run_doctor(_settings(user="postgres"),
                            provider=FakeModelProvider(responses=["OK"]),
                            parquet_sample=lambda path: 5)
        postgres = next(item for item in report.checks if item.name == "postgres.readonly")
        self.assertEqual(postgres.status, "FAIL")
        self.assertTrue(report.failed)

    def test_render_never_includes_the_secret(self):
        report = run_doctor(_settings(), provider=FakeModelProvider(responses=["OK"]),
                            parquet_sample=lambda path: 5)
        rendered = report.render()
        self.assertNotIn("test-db-value", rendered)
        self.assertNotIn("test-llm-value", rendered)

    def test_provider_failure_is_fail_not_crash(self):
        report = run_doctor(_settings(),
                            provider=FakeModelProvider(error=ProviderError("down")),
                            parquet_sample=lambda path: 5)
        llm = next(item for item in report.checks if item.name == "llm.reachability")
        self.assertEqual(llm.status, "FAIL")

    def test_parquet_sample_is_reported(self):
        report = run_doctor(_settings(), check_llm=False, parquet_sample=lambda path: 42)
        parquet = next(item for item in report.checks if item.name == "parquet.archive")
        self.assertTrue(parquet.status in ("PASS", "FAIL"))


class CliDoctorTests(unittest.TestCase):
    def test_doctor_command_renders_a_report(self):
        import io
        from contextlib import redirect_stdout
        from app import cli
        from app.diagnostics import CheckResult
        stub = DoctorReport(checks=(CheckResult("stub", "PASS", "ok"),))
        with patch("app.diagnostics.run_doctor", return_value=stub), \
                redirect_stdout(io.StringIO()) as output:
            exit_code = cli.main(["doctor", "--no-llm"])
        self.assertEqual(exit_code, 0)
        self.assertIn("RESULT: PASS", output.getvalue())


if __name__ == "__main__":
    unittest.main()
