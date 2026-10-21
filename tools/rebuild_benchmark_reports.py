#!/usr/bin/env python3
"""Rebuild the committed live benchmark reports with the corrected M1-T3 measurement.

The committed ``docs/benchmarks/{phase5b,m1}_live.json`` reports embed the per-run
observations captured live on 2026-09-27. This script re-scores those *unchanged* runs with
the corrected contract (raw-observation latency pooling and explicit answer adjudication) so
the published aggregates are reproducible rather than transcribed.

It is not on the runtime path and never touches ``app/``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.benchmark_research import build_report, normalize_answer_adjudications  # noqa: E402


def rebuild(
    tasks_path: Path,
    runs_path: Path,
    *,
    build: str,
    adjudications_path: Path | None,
    answer_adjudications_path: Path | None,
) -> dict:
    tasks = json.loads(tasks_path.read_text(encoding="utf-8"))
    source = json.loads(runs_path.read_text(encoding="utf-8"))
    runs = source["runs"] if isinstance(source, dict) and "runs" in source else source
    adjudications = (
        json.loads(adjudications_path.read_text(encoding="utf-8")) if adjudications_path else {}
    )
    answer_adjudications = (
        normalize_answer_adjudications(json.loads(answer_adjudications_path.read_text(encoding="utf-8")))
        if answer_adjudications_path
        else {}
    )
    return build_report(
        tasks,
        runs,
        mode="live",
        build=build,
        adjudications=adjudications,
        answer_adjudications=answer_adjudications,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild committed benchmark reports (M1-T3 repair)")
    parser.add_argument("--tasks", required=True)
    parser.add_argument("--runs", required=True, help="a committed live report or a raw runs JSON")
    parser.add_argument("--build", required=True)
    parser.add_argument("--adjudications")
    parser.add_argument("--answer-adjudications")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    report = rebuild(
        Path(args.tasks),
        Path(args.runs),
        build=args.build,
        adjudications_path=Path(args.adjudications) if args.adjudications else None,
        answer_adjudications_path=Path(args.answer_adjudications) if args.answer_adjudications else None,
    )
    report["generated_at"] = json.loads(Path(args.runs).read_text(encoding="utf-8")).get("generated_at")
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report["aggregate"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
