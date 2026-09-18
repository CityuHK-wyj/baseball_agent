"""Live end-to-end runs against the configured model provider.

Measures real semantic / planner / replan / response-composer latency, real Planner
context sizes and real model-call counts. Uses the *product* runtime (LLM cognition) and
the live web tool. Expensive, so sample counts are stated explicitly.

Usage:
    python3 docs/reviews/v05-runtime-performance/run_live.py [scenario_id ...]
"""

from __future__ import annotations

import sys
from pathlib import Path

AUDIT = Path(__file__).resolve().parent
REPO = AUDIT.parents[2]
sys.path.insert(0, str(AUDIT))
sys.path.insert(0, str(REPO))

from harness import Recorder  # noqa: E402
from builders import build_live  # noqa: E402


def _turn_meta(result) -> dict:
    coverage = result.coverage
    goal = result.trace.goal if result.trace else None
    return {
        "core_goal_supported": bool(getattr(coverage, "core_goal_supported", False)),
        "coverage_verdict": getattr(coverage, "verdict", ""),
        "gaps": list(getattr(coverage, "gaps", ()) or ())[:8],
        "missing_obligations": list(getattr(coverage, "missing_obligations", ()) or ()),
        "conflicts": list(getattr(coverage, "conflicts", ()) or ()),
        "claims": [{"type": c.claim_type, "chars": len(c.text)} for c in result.claims],
        "artifact_count": len(result.artifacts),
        "goal_obligations": [o.kind for o in (goal.obligations if goal else ())],
        "goal_revision": getattr(goal, "revision", 0),
        "trace_needs": [
            {"need_id": n.need_id, "status": n.status,
             "capability": n.proposed_capability}
            for n in (result.trace.needs if result.trace else ())],
        "trace_tool_calls": list(result.trace.tool_calls) if result.trace else [],
    }


def _recovery_record(recorder: Recorder, result) -> None:
    trace = result.trace
    if trace is None:
        return
    recorder.record_recovery(
        final_status=result.status,
        attempts=[{"capability": a.capability, "outcome_code": a.outcome_code,
                   "status": a.status, "retryable": a.retryable, "need_id": a.need_id,
                   "detail": a.detail[:200]} for a in trace.attempts],
        needs=[{"need_id": n.need_id, "status": n.status,
                "capability": n.proposed_capability} for n in trace.needs],
        tool_calls=list(trace.tool_calls),
        coverage_verdict=getattr(result.coverage, "verdict", ""))


SCENARIOS = {
    "live_simple_local": {"message": "How many pitches were thrown in June 2021?"},
    "live_analytical": {"message": "Which pitchers averaged the highest fastball "
                                   "velocity in June 2021?"},
    "live_multistep": {"message": "Which New York Yankees pitchers threw the most "
                                  "pitches in June 2021?"},
    "live_db_to_web": {"message": "Who threw the most pitches in June 2021, and is "
                                  "there recent news about them?"},
    "live_web_unavailable": {"message": "What is the latest news about MLB expansion?"},
    "live_entity_ambiguity": {"message": "How did Luis pitch in 2021?"},
    "live_impossible_future": {"message": "Who will win the 2030 World Series?"},
    "live_contradictory": {"message": "Which players hit at least 100 home runs and "
                                      "at most 5 home runs in 2021?"},
    "live_followup": {"message": "How many pitches were thrown in June 2021?",
                      "followup": "What about July?"},
}


def run_scenario(scenario_id: str, spec: dict, recorder: Recorder,
                 call_cap: float | None = None) -> None:
    runtime, knowledge_store, store = build_live(recorder=recorder,
                                                 call_cap_seconds=call_cap)
    try:
        conversation = runtime.start_conversation()
        recorder.begin_turn(scenario_id, 1)
        result = runtime.send_message(conversation, spec["message"])
        recorder.end_turn(result.status, len(result.answer), meta=_turn_meta(result))
        _recovery_record(recorder, result)
        if spec.get("followup"):
            recorder.begin_turn(scenario_id, 2)
            second = runtime.respond_to_clarification(conversation, spec["followup"]) \
                if result.status == "WAITING_FOR_USER" else \
                runtime.send_message(conversation, spec["followup"])
            recorder.end_turn(second.status, len(second.answer), meta=_turn_meta(second))
            _recovery_record(recorder, second)
    finally:
        runtime.close()
        knowledge_store.close()
        if store is not None:
            store.close()


def main() -> int:
    requested = sys.argv[1:] or list(SCENARIOS)
    # Appends to the shared audit dataset (the ``scenario`` field distinguishes runs).
    recorder = Recorder(AUDIT, secrets=())
    import os

    call_cap = float(os.getenv("AUDIT_CALL_CAP", "0") or 0) or None
    if call_cap:
        print(f"[live] diagnostic wall-clock cap = {call_cap:.0f}s per model call",
              flush=True)
    for scenario_id in requested:
        if scenario_id not in SCENARIOS:
            print(f"unknown scenario {scenario_id!r}")
            continue
        print(f"[live] {scenario_id}", flush=True)
        try:
            run_scenario(scenario_id, SCENARIOS[scenario_id], recorder, call_cap)
        except Exception as exc:  # noqa: BLE001 - record and continue
            recorder.error(f"scenario:{scenario_id}", exc)
            print(f"  ERROR {type(exc).__name__}: {exc}")
        recorder.flush()
        print("  flushed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
