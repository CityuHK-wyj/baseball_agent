"""v0.5 internal adversarial exploration (NOT production logic).

This script exercises the product artifact runtime (live LLM + live PostgreSQL when
configured) over a deliberately diverse, internally invented query set. It records the
evaluation-principle facts, never the model's hidden reasoning, and it is stored under
docs/reviews so no exploratory wording leaks into production prompts or branches.

Usage:
    PYTHONPATH=. python3 docs/reviews/v05-planner-convergence/explore.py [--only NAME]

Findings are appended as JSONL to docs/reviews/v05-planner-convergence/explore.jsonl.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from app.artifact_runtime.factory import build_runtime  # noqa: E402

OUT = Path(__file__).with_name("explore.jsonl")

# Internally invented exploratory queries. These are review artifacts, not product logic.
QUERIES: dict[str, str] = {
    "db_simple": "How many pitches did Shohei Ohtani see in April 2023?",
    "db_roster_compose": (
        "For the New York Yankees current active roster, what was the average "
        "exit velocity in the 2023 regular season?"),
    "db_ranking": "Which pitchers had the highest average release speed in 2023?",
    "db_to_web": (
        "The league-wide average pitch velocity in 2023 is our result; what reported "
        "rule or injury context might explain that number?"),
    "ambiguous_identity": "How did Chris Smith pitch in the 2023 season?",
    "impossible_future": "Who won the 2027 World Series?",
    "contradictory": (
        "List 2023 batters with at least 50 home runs and at most 5 home runs."),
    "multilingual": "2023年 大谷翔平 的平均击球初速是多少？",
    "missing_capability": (
        "What was the 2023 Defensive Runs Saved leaderboard for shortstops?"),
    "valid_zero_result": (
        "How many pitches did a player with no Statcast appearances throw in 2023?"),
    "malformed": "??? bbwa 2023 1000%",
    "changed_constraint": "Now do that for 2024 instead.",
}


def _event_types(trace) -> list[str]:
    return sorted({event.event_type for event in (trace.events if trace else ())})


def _summarize(name: str, query: str, result, conversation) -> dict:
    trace = result.trace
    goal = trace.goal if trace else None
    attempts = list(trace.attempts) if trace else []
    artifacts = list(result.artifacts)
    return {
        "name": name,
        "status": result.status,
        "obligations": [
            {"kind": item.kind, "value": item.value} for item in (goal.obligations if goal else ())
        ],
        "conflicts": [
            {"kind": item.kind, "severity": item.severity} for item in (goal.conflicts if goal else ())
        ],
        "need_statuses": [
            {"need_id": item.need_id, "capability": item.proposed_capability,
             "status": item.status, "route_of": item.route_of}
            for item in (trace.needs if trace else ())
        ],
        "attempts": [
            {"need_id": item.need_id, "capability": item.capability,
             "outcome_code": item.outcome_code, "status": item.status,
             "artifact_ids": list(item.artifact_ids)}
            for item in attempts
        ],
        "artifact_kinds": [item.kind for item in artifacts],
        "artifact_statuses": [item.status for item in artifacts],
        "grounded_exports": [
            {"type": export.export_type, "id": export.export_id}
            for artifact in artifacts for export in artifact.exports
        ],
        "claims": [
            {"type": claim.claim_type, "supports": list(claim.support_refs)}
            for claim in result.claims
        ],
        "obligation_coverage": (result.coverage.obligation_coverage if result.coverage else {}),
        "core_goal_supported": (result.coverage.core_goal_supported if result.coverage else None),
        "gaps": list(result.coverage.gaps) if result.coverage else [],
        "event_types": _event_types(trace),
        "answer_chars": len(result.answer or ""),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.list:
        for name in QUERIES:
            print(name)
        return 0

    runtime = build_runtime(use_llm=True, today=date.today)
    try:
        records = []
        names = [args.only] if args.only else list(QUERIES)
        for name in names:
            query = QUERIES[name]
            try:
                conversation_id = runtime.start_conversation()
                if name == "changed_constraint":
                    # A genuine follow-up: establish a base request, then change a constraint.
                    runtime.send_message(
                        conversation_id,
                        "What was the league average exit velocity in the 2023 season?")
                result = runtime.send_message(conversation_id, query)
                conversation = runtime.get_conversation(conversation_id)
                record = _summarize(name, query, result, conversation)
            except Exception as error:  # noqa: BLE001 - exploration must not crash
                record = {"name": name, "error": f"{type(error).__name__}: {error}"}
            records.append(record)
            print(json.dumps(record, ensure_ascii=False))
        with OUT.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    finally:
        runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
