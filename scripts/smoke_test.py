#!/usr/bin/env python3
"""User-facing smoke test: ``python3 scripts/smoke_test.py``.

Thin, non-destructive wrapper around ``python3 -m app.cli doctor``. It checks the
environment, the read-only PostgreSQL analytics identity, the Parquet archive, the shared
knowledge store and the semantic model provider, without printing any secret.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.diagnostics import run_doctor  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    report = run_doctor(check_llm="--no-llm" not in argv)
    print(report.render())
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
