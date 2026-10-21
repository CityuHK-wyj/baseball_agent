#!/usr/bin/env python3
"""Classify the first material retrieval failure for each incomplete benchmark task (M2-6).

Reads a committed benchmark report (``docs/benchmarks/*.json``) and prints a per-task failure
breakdown using :mod:`app.web.gap_assessment`. This is diagnostic only: it never runs retrieval
and never changes production behavior.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.web.gap_assessment import breakdown  # noqa: E402


def assess(report: dict) -> dict:
    return breakdown(report.get("runs") or {})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Web Research retrieval gap assessment (M2-6)")
    parser.add_argument("--report", required=True, help="benchmark report JSON")
    parser.add_argument("--out", help="optional output JSON")
    args = parser.parse_args(argv)

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    result = assess(report)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if args.out:
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
