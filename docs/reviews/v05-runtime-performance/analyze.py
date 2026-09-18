"""Aggregate the redacted JSONL diagnostics into report-ready tables.

Usage:
    python3 docs/reviews/v05-runtime-performance/analyze.py
Writes ``summary.json``.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

AUDIT = Path(__file__).resolve().parent

EXCLUSIVE_ORDER = [
    "semantic", "resolve_entities", "goal_construction", "planner_context",
    "planner_initial", "planner_next_action", "planner_add_needs", "binding_resolution",
    "tool_execute", "scope_verification", "artifact_registration", "judge_assess",
    "coverage_summarize", "claim_build", "claim_validate", "state_projection",
    "response_compose", "trace_build", "record_entities", "record_candidates",
    "persistence",
]


def _load(name: str) -> list[dict]:
    path = AUDIT / name
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _median(values):
    return round(statistics.median(values), 3) if values else 0.0


def main() -> int:
    timelines = _load("timelines.jsonl")
    tools = _load("tool_calls.jsonl")
    llms = _load("llm_calls.jsonl")

    by_scenario = defaultdict(list)
    for row in timelines:
        by_scenario[row["scenario"]].append(row)

    scenario_stats = {}
    for scenario, rows in by_scenario.items():
        turn_rows = defaultdict(list)
        for row in rows:
            turn_rows[row["turn"]].append(row)
        turns = {}
        for turn, items in turn_rows.items():
            totals = [r["total_ms"] for r in items]
            phases = defaultdict(list)
            for r in items:
                for key, value in r["exclusive_ms"].items():
                    phases[key].append(value)
            llm_for_turn = [c for c in llms
                            if c["scenario"] == scenario and c["turn"] == turn]
            turns[turn] = {
                "samples": len(items),
                "status_counts": dict(Counter(r["status"] for r in items)),
                "total_ms": {"min": round(min(totals), 2), "median": _median(totals),
                             "max": round(max(totals), 2)},
                "residual_orchestration_ms": _median(
                    [r["residual_orchestration_ms"] for r in items]),
                "exclusive_ms_median": {k: _median(v) for k, v in phases.items()},
                "llm_calls": len(llm_for_turn),
                "llm_purposes": dict(Counter(c["purpose"] for c in llm_for_turn)),
                "llm_total_ms": round(sum(c["duration_ms"] for c in llm_for_turn), 2),
            }
        scenario_stats[scenario] = turns

    tool_stats = defaultdict(lambda: defaultdict(list))
    for row in tools:
        tool_stats[row["scenario"]][row["capability"]].append(row)
    tool_summary = {}
    for scenario, caps in tool_stats.items():
        tool_summary[scenario] = {}
        for capability, rows in caps.items():
            durations = [r["tool_duration_ms"] for r in rows
                         if r.get("tool_duration_ms") is not None]
            tool_summary[scenario][capability] = {
                "samples": len(rows),
                "outcome_codes": dict(Counter(r.get("outcome_code", "") for r in rows)),
                "failure_classes": dict(Counter(r.get("failure_class", "") for r in rows)),
                "duration_ms": {"min": round(min(durations), 3) if durations else 0,
                                "median": _median(durations),
                                "max": round(max(durations), 3) if durations else 0},
            }

    llm_by_purpose = defaultdict(list)
    for row in llms:
        llm_by_purpose[row["purpose"]].append(row)
    llm_summary = {}
    for purpose, rows in llm_by_purpose.items():
        durations = [r["duration_ms"] for r in rows]
        llm_summary[purpose] = {
            "samples": len(rows),
            "duration_ms": {"min": round(min(durations), 1), "median": _median(durations),
                            "max": round(max(durations), 1)},
            "prompt_chars": {"min": min(r["prompt_chars"] for r in rows),
                             "median": _median([r["prompt_chars"] for r in rows]),
                             "max": max(r["prompt_chars"] for r in rows)},
            "output_chars": {"median": _median([r["output_chars"] for r in rows])},
            "prompt_tokens_median": _median([r["prompt_tokens"] for r in rows]),
            "completion_tokens_median": _median([r["completion_tokens"] for r in rows]),
            "statuses": dict(Counter(r["status"] for r in rows)),
            "models": sorted({r["model"] for r in rows}),
        }

    summary = {
        "deterministic_turns": len(timelines),
        "tool_calls": len(tools),
        "llm_calls": len(llms),
        "scenarios": scenario_stats,
        "tool_summary": tool_summary,
        "llm_summary": llm_summary,
    }
    (AUDIT / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {AUDIT / 'summary.json'}")
    print(f"turns={len(timelines)} tool_calls={len(tools)} llm_calls={len(llms)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
