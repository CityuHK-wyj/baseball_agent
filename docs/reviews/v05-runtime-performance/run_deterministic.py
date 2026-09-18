"""Deterministic, instrumented tool-execution and recovery runs.

Runs the *real* runtime code paths with scripted planners so each Tool can be traced
precisely and cheaply (many samples). No production module is modified. Live LLM
behaviour is measured separately by ``run_live.py``.

A fresh runtime and a fresh Need graph are built for every repetition: Need objects are
mutable and are mutated by the runtime, so reusing them across conversations would be a
harness artifact rather than measured product behaviour.

Usage:
    python3 docs/reviews/v05-runtime-performance/run_deterministic.py
"""

from __future__ import annotations

import sys
from pathlib import Path

AUDIT = Path(__file__).resolve().parent
REPO = AUDIT.parents[2]
sys.path.insert(0, str(AUDIT))
sys.path.insert(0, str(REPO))

from harness import Recorder  # noqa: E402
from builders import build_offline  # noqa: E402
import scenarios as S  # noqa: E402


def _batting_tool(rows):
    from app.tools.batting import BattingLine, BattingStatsTool

    lines = tuple(BattingLine(**row) for row in rows)

    class _Client:
        def season(self, year):
            return lines

        def date_range(self, start, end):
            return lines

    return BattingStatsTool(client=_Client())


BATTING_ROWS = (
    dict(name="Aaron Judge", player_id="592450", team="New York Yankees",
         plate_appearances=550, home_runs=39, walks=80, strikeouts=150,
         avg=0.287, obp=0.373, slg=0.544, ops=0.917),
    dict(name="Bryce Harper", player_id="547180", team="Philadelphia Phillies",
         plate_appearances=560, home_runs=35, walks=90, strikeouts=140,
         avg=0.290, obp=0.390, slg=0.540, ops=0.930),
)


def _turn_meta(result) -> dict:
    coverage = result.coverage
    return {
        "core_goal_supported": bool(getattr(coverage, "core_goal_supported", False)),
        "coverage_verdict": getattr(coverage, "verdict", ""),
        "gaps": list(getattr(coverage, "gaps", ()) or ())[:8],
        "missing_obligations": list(getattr(coverage, "missing_obligations", ()) or ()),
        "conflicts": list(getattr(coverage, "conflicts", ()) or ()),
        "claims": [{"type": c.claim_type, "chars": len(c.text)} for c in result.claims],
        "artifact_count": len(result.artifacts),
        "trace_attempts": [
            {"capability": a.capability, "outcome_code": a.outcome_code,
             "status": a.status, "retryable": a.retryable}
            for a in (result.trace.attempts if result.trace else ())],
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
    attempts = [
        {"capability": a.capability, "outcome_code": a.outcome_code,
         "status": a.status, "retryable": a.retryable, "detail": a.detail[:200],
         "need_id": a.need_id}
        for a in trace.attempts]
    needs = [{"need_id": n.need_id, "status": n.status,
              "capability": n.proposed_capability,
              "unsatisfied_inputs": list(n.unsatisfied_inputs)}
             for n in trace.needs]
    failure_classes = []
    try:
        from app.artifact_runtime.recovery import failure_class

        failure_classes = sorted({failure_class(a["outcome_code"]) for a in attempts
                                  if a["outcome_code"] not in ("SUCCESS", "")})
    except Exception:  # noqa: BLE001
        pass
    recorder.record_recovery(
        final_status=result.status,
        attempts=attempts,
        needs=needs,
        failure_classes=failure_classes,
        coverage_verdict=getattr(result.coverage, "verdict", ""),
        core_goal_supported=bool(getattr(result.coverage, "core_goal_supported", False)),
        tool_calls=list(trace.tool_calls),
    )


def run(spec: dict, recorder: Recorder) -> None:
    for _rep in range(spec.get("reps", 1)):
        runtime, knowledge_store, store = build_offline(recorder=recorder,
                                                        **spec["build"]())
        try:
            conversation = runtime.start_conversation()
            recorder.begin_turn(spec["id"], 1)
            result = runtime.send_message(conversation, spec["message"])
            recorder.end_turn(result.status, len(result.answer), meta=_turn_meta(result))
            _recovery_record(recorder, result)
            if spec.get("clarify"):
                recorder.begin_turn(spec["id"], 2)
                second = runtime.respond_to_clarification(conversation, spec["clarify"])
                recorder.end_turn(second.status, len(second.answer),
                                  meta=_turn_meta(second))
                _recovery_record(recorder, second)
        finally:
            runtime.close()
            knowledge_store.close()
            if store is not None:
                store.close()


def _recovery_builder():
    bad = S.need("need-local", "local_analytics",
                 parameters={"analytical_query": S.ir_invalid_field(
                     start="2021-06-01", end="2021-06-30"),
                     "start": "2021-06-01", "end": "2021-06-30"},
                 scope=S.Scope(population="players"))
    good = S.need("need-local-fixed", "local_analytics",
                  parameters={"analytical_query": S.ir_pitch_count(
                      start="2021-06-01", end="2021-06-30"),
                      "start": "2021-06-01", "end": "2021-06-30"},
                  scope=S.Scope(population="players"))
    return {"needs": [bad], "planner": S.RecoveringPlanner([bad], [good])}


def _clarification_builder():
    return {"needs": S.needs_local_count(),
            "interpreter": S.FixedInterpreter(question="Which population did you mean?",
                                              options=["pitchers", "batters"])}


def build_specs() -> list[dict]:
    batter_ids = S.parquet_batter_ids(limit=4)
    names = ["Aaron Judge", "Bryce Harper"]
    return [
        {"id": "local_count_parquet", "reps": 5,
         "build": lambda: {"needs": S.needs_local_count()},
         "message": "diagnostic simple local request"},
        {"id": "local_avg_velocity", "reps": 5,
         "build": lambda: {"needs": [S.need(
             "need-avg", "local_analytics",
             parameters={"analytical_query": S.ir_avg_velocity_by_pitcher(
                 start="2021-06-01", end="2021-06-30"),
                 "start": "2021-06-01", "end": "2021-06-30"},
             scope=S.Scope(population="players"))]},
         "message": "diagnostic analytical aggregate request"},
        {"id": "local_derived_compute", "reps": 3,
         "build": lambda: {"needs": S.needs_compute()},
         "message": "diagnostic derived measure and compute request"},
        {"id": "roster_then_local", "reps": 3,
         "build": lambda: {"needs": S.needs_roster_then_local(),
                           "roster_provider": S.roster_provider_from_ids(batter_ids)},
         "message": "diagnostic roster composition request"},
        {"id": "knowledge", "reps": 5,
         "build": lambda: {"needs": S.needs_knowledge()},
         "message": "diagnostic shared knowledge request"},
        {"id": "entity_resolution", "reps": 5,
         "build": lambda: {"needs": S.needs_entity_resolution(
             mentions=["Aaron Judge", "Bryce Harper"])},
         "message": "diagnostic entity resolution request"},
        {"id": "entity_ambiguity", "reps": 3,
         "build": lambda: {"needs": S.needs_entity_resolution(mentions=["Luis"])},
         "message": "diagnostic entity ambiguity request"},
        {"id": "batting_stats", "reps": 3,
         "build": lambda: {"needs": S.needs_batting(names=["Aaron Judge"]),
                           "batting": _batting_tool(BATTING_ROWS)},
         "message": "diagnostic batting stats request"},
        {"id": "web_fake_grounded", "reps": 3,
         "build": lambda: {"needs": S.needs_web(),
                           "web": S.FakeWeb(findings=S.grounded_player_findings())},
         "message": "diagnostic grounded web request"},
        {"id": "web_then_entities", "reps": 3,
         "build": lambda: {"needs": S.needs_web_then_entities(focus=["Aaron Judge"]),
                           "web": S.FakeWeb(findings=S.grounded_named_findings(names))},
         "message": "diagnostic web to entity request"},
        {"id": "web_live_unavailable", "reps": 3,
         "build": lambda: {"needs": S.needs_web()},
         "message": "diagnostic live web unavailable request"},
        {"id": "db_then_web_live", "reps": 2,
         "build": lambda: {"needs": S.needs_local_then_web()},
         "message": "diagnostic database to web request"},
        {"id": "web_to_db", "reps": 2,
         "build": lambda: {"needs": [
             S.need("need-web", "web_research",
                    parameters={"query": "recent player form"}),
             S.need("need-entities", "evidence_entities",
                    parameters={"focus": names}, depends_on=("need-web",)),
             S.need("need-local", "local_analytics",
                    parameters={"analytical_query": S.ir_pitch_count(
                        start="2021-06-01", end="2021-06-30",
                        entity_set={"field": "batter", "export_ref": "need-entities"}),
                        "start": "2021-06-01", "end": "2021-06-30"},
                    depends_on=("need-entities",), scope=S.Scope(population="players")),
         ], "web": S.FakeWeb(findings=S.grounded_named_findings(names))},
         "message": "diagnostic web to database request"},
        {"id": "invalid_ir", "reps": 3,
         "build": lambda: {"needs": [S.need(
             "need-bad", "local_analytics",
             parameters={"analytical_query": S.ir_invalid_field(
                 start="2021-06-01", end="2021-06-30")},
             scope=S.Scope(population="players"))]},
         "message": "diagnostic invalid IR request"},
        {"id": "empty_result", "reps": 3,
         "build": lambda: {"needs": [S.need(
             "need-empty", "local_analytics",
             parameters={"analytical_query": S.ir_empty_window(
                 start="2021-01-01", end="2021-01-05"),
                 "start": "2021-01-01", "end": "2021-01-05"},
             scope=S.Scope(population="players"))]},
         "message": "diagnostic valid empty request"},
        {"id": "postgres_down", "reps": 3,
         "build": lambda: {"needs": [S.need(
             "need-pg", "local_analytics",
             parameters={"analytical_query": S.ir_postgres_count(
                 start="2024-06-01", end="2024-06-30"),
                 "start": "2024-06-01", "end": "2024-06-30"},
             scope=S.Scope(population="players"))]},
         "message": "diagnostic postgres executor request"},
        {"id": "impossible_capability", "reps": 3,
         "build": lambda: {"needs": S.needs_impossible()},
         "message": "diagnostic impossible capability request"},
        {"id": "recovery_vague_no_obligation", "reps": 3,
         "build": _recovery_builder,
         "message": "diagnostic recovery probe"},
        {"id": "recovery_with_obligation", "reps": 3,
         "build": _recovery_builder,
         "message": "How many pitches were thrown in 2021?"},
        {"id": "clarification_turn", "reps": 3,
         "build": _clarification_builder,
         "message": "diagnostic ambiguous request", "clarify": "pitchers"},
    ]


def main() -> int:
    recorder = Recorder(AUDIT, secrets=())
    for spec in build_specs():
        print(f"[deterministic] {spec['id']} x{spec.get('reps', 1)}", flush=True)
        try:
            run(spec, recorder)
        except Exception as exc:  # noqa: BLE001 - record and continue
            recorder.error(f"scenario:{spec['id']}", exc)
            print(f"  ERROR {type(exc).__name__}: {exc}")
    recorder.write()
    print(f"wrote {AUDIT / 'profile.jsonl'} etc.")
    print(f"turns={len(recorder.timelines)} tool_calls={len(recorder.tool_calls)} "
          f"llm_calls={len(recorder.llm_calls)} errors={len(recorder.errors)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
