#!/usr/bin/env python3
"""Summarize M2.10 resolver activations from saved, redacted goal diagnostics.

The live benchmark report omits full representations. Diagnostics retain typed execution
statuses but redact program bodies and hashes, so this measures mechanics, not replay.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def summarize(runtime_dir: Path, session_prefix: str, task_ids: list[str]) -> dict:
    paths = sorted(path for task_id in task_ids
                   for path in (runtime_dir / "diagnostics").glob(f"{session_prefix}_{task_id}_*__goal_*.json"))
    result = {
        "session_prefix": session_prefix,
        "goal_diagnostics": len(paths),
        "web_research_calls": 0,
        "documents": 0,
        "usable_without_resolver": 0,
        "resolver_triggered_documents": 0,
        "resolver_traces": 0,
        "generated_programs": 0,
        "trace_statuses": {},
    }
    statuses = Counter()
    for path in paths:
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for record in records:
            if record.get("stage") != "web_research":
                continue
            result["web_research_calls"] += 1
            for document in record.get("data", {}).get("documents") or []:
                result["documents"] += 1
                traces = document.get("resolution_traces") or []
                if traces:
                    result["resolver_triggered_documents"] += 1
                elif document.get("retrieval_status") == "OK":
                    result["usable_without_resolver"] += 1
                for trace in traces:
                    result["resolver_traces"] += 1
                    result["generated_programs"] += int("program_hash" in trace)
                    statuses[str(trace.get("status") or "UNKNOWN")] += 1
    result["trace_statuses"] = dict(sorted(statuses.items()))
    result["deterministic_usable_fraction"] = (
        result["usable_without_resolver"] / result["documents"] if result["documents"] else None
    )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-dir", default=".runtime")
    parser.add_argument("--session", required=True)
    parser.add_argument("--tasks", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    task_ids = [str(task["id"]) for task in json.loads(Path(args.tasks).read_text(encoding="utf-8"))]
    report = summarize(Path(args.runtime_dir), args.session, task_ids)
    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
