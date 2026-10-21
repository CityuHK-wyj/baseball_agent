"""M1-T3 repair: benchmark harness determinism, schema, aggregation and non-mutation.

The repair tests lock three things the independent M1 review found wrong:

1. Aggregate latency is pooled from raw run observations, never from task percentiles.
2. ACR requires an explicit answer-completion adjudication (never consistency/GoalStatus).
3. A run that did not invoke ``web_research`` contributes no zero-latency sample.
"""

from __future__ import annotations

import json
from pathlib import Path

from tools.benchmark_research import build_report, main, normalize_answer_adjudications, percentile, sample_kind


TASKS = [
    {
        "id": "historical_strike",
        "query": "What caused the 1994 Major League Baseball strike?",
        "language": "en",
        "category": "historical_explanation",
        "obligation": {"kind": "EXPLANATION", "mandatory": 2},
    },
    {
        "id": "current_injury",
        "query": "Is there a recent injury report?",
        "language": "en",
        "category": "current_factual",
        "obligation": {"kind": "FACT", "mandatory": 1},
    },
]

RUNS = {
    "historical_strike": [
        {
            "goal_status": "COMPLETE",
            "mandatory_total": 2,
            "mandatory_satisfied": 2,
            "answer_completion_adjudicated": True,
            "evidence_findings": [
                {"id": "f1", "status": "SUPPORTED", "genuine": True},
                {"id": "f2", "status": "SUPPORTED", "genuine": False},
                {"id": "f3", "status": "INSUFFICIENT", "genuine": None},
            ],
            "latency": {"web:search": 1000, "web:fetch": 2000, "tool:web_research": 3000, "goal_end_to_end": 5000},
            "provider_calls": 2,
            "fetched_documents": 1,
            "provider_errors": 0,
            "information_gain": {"mandatory_propositions_moved": 2, "verified_facts": 3},
        },
        {
            "goal_status": "LIMITED",
            "mandatory_total": 2,
            "mandatory_satisfied": 1,
            "answer_completion_adjudicated": False,
            "evidence_findings": [
                {"id": "f4", "status": "SUPPORTED", "genuine": True},
            ],
            "latency": {"web:search": 3000, "web:fetch": 4000, "tool:web_research": 9000, "goal_end_to_end": 15000},
            "provider_calls": 3,
            "fetched_documents": 2,
            "provider_errors": 1,
            "information_gain": {"mandatory_propositions_moved": 1, "verified_facts": 2},
        },
    ],
    "current_injury": [
        {
            "goal_status": "LIMITED",
            "mandatory_total": 1,
            "mandatory_satisfied": 0,
            "answer_completion_adjudicated": False,
            "evidence_findings": [
                {"id": "f5", "status": "INSUFFICIENT", "genuine": None},
            ],
            "latency": {"web:search": 2000, "tool:web_research": 4000, "goal_end_to_end": 7000},
            "provider_calls": 1,
            "fetched_documents": 0,
            "provider_errors": 1,
            "information_gain": {},
        }
    ],
}


def test_percentile_interpolates():
    assert percentile([], 50) == 0
    assert percentile([1000], 50) == 1000
    assert percentile([1000, 3000], 50) == 2000
    assert percentile([1000, 2000, 3000], 95) == 2900


def test_sample_kind_distinguishes_goal_tool_and_provider():
    assert sample_kind("goal_end_to_end") == "goal_run"
    assert sample_kind("tool:web_research") == "tool_invocation"
    assert sample_kind("web:source:wikipedia") == "provider_call"
    assert sample_kind("web:search") == "stage_observation"


def test_report_schema_and_aggregates():
    report = build_report(TASKS, RUNS, mode="offline", build="test")
    assert report["mode"] == "offline"
    assert report["build"] == "test"
    assert report["aggregate"]["task_count"] == 2
    # one of two tasks has an explicitly adjudicated all-mandatory COMPLETE run
    assert report["aggregate"]["answer_completion_rate"] == 0.5
    assert report["aggregate"]["answer_completion_numerator"] == 1
    assert report["aggregate"]["answer_completion_denominator"] == 2
    assert report["aggregate"]["answer_completion_adjudicated_runs"] == 3
    assert report["aggregate"]["answer_completion_unadjudicated_runs"] == 0
    # genuine supported: f1, f4 = 2; admitted supported: f1, f2, f4 = 3
    assert report["aggregate"]["evidence_precision_numerator"] == 2
    assert report["aggregate"]["evidence_precision_denominator"] == 3
    assert report["aggregate"]["evidence_precision_false_support"] == 1
    assert report["aggregate"]["evidence_precision_adjudicated"] == 3
    assert report["aggregate"]["evidence_precision_unadjudicated"] == 0
    assert report["aggregate"]["latency_p50"]["web:search"] == 2000
    assert report["aggregate"]["latency_p95"]["web:search"] > report["aggregate"]["latency_p50"]["web:search"]
    assert report["aggregate"]["provider_calls"] == 6
    assert report["aggregate"]["fetched_documents"] == 3
    assert report["aggregate"]["provider_errors"] == 2
    per_task = {item["task_id"]: item for item in report["tasks"]}
    assert per_task["historical_strike"]["answer_completion"] == 1
    assert per_task["current_injury"]["answer_completion"] == 0


def test_aggregate_latency_pools_raw_run_observations_not_task_percentiles():
    """The aggregate p50 must be the median of all run samples, not of task medians."""
    tasks = [
        {"id": "a", "query": "a", "language": "en", "obligation": {"kind": "FACT", "mandatory": 1}},
        {"id": "b", "query": "b", "language": "en", "obligation": {"kind": "FACT", "mandatory": 1}},
    ]
    runs = {
        "a": [
            {"goal_status": "LIMITED", "mandatory_total": 1, "mandatory_satisfied": 0,
             "answer_completion_adjudicated": False, "evidence_findings": [],
             "latency": {"goal_end_to_end": 1, "tool:web_research": 1}, "provider_calls": 0,
             "fetched_documents": 0, "provider_errors": 0},
            {"goal_status": "LIMITED", "mandatory_total": 1, "mandatory_satisfied": 0,
             "answer_completion_adjudicated": False, "evidence_findings": [],
             "latency": {"goal_end_to_end": 2, "tool:web_research": 2}, "provider_calls": 0,
             "fetched_documents": 0, "provider_errors": 0},
        ],
        "b": [
            {"goal_status": "LIMITED", "mandatory_total": 1, "mandatory_satisfied": 0,
             "answer_completion_adjudicated": False, "evidence_findings": [],
             "latency": {"goal_end_to_end": 100, "tool:web_research": 100}, "provider_calls": 0,
             "fetched_documents": 0, "provider_errors": 0},
            {"goal_status": "LIMITED", "mandatory_total": 1, "mandatory_satisfied": 0,
             "answer_completion_adjudicated": False, "evidence_findings": [],
             "latency": {"goal_end_to_end": 200, "tool:web_research": 200}, "provider_calls": 0,
             "fetched_documents": 0, "provider_errors": 0},
        ],
    }
    report = build_report(tasks, runs, mode="offline", build="test")
    # Task medians are 1.5 and 150; the pooled median is 51.0.
    assert report["aggregate"]["latency_p50"]["goal_end_to_end"] == 51.0
    stats = report["aggregate"]["latency"]["goal_end_to_end"]
    assert stats["n"] == 4
    assert stats["min"] == 1
    assert stats["max"] == 200
    assert stats["sample_kind"] == "goal_run"
    assert report["aggregate"]["latency"]["tool:web_research"]["sample_kind"] == "tool_invocation"


def test_acr_is_never_derived_from_consistency_or_goal_status():
    """A COMPLETE/all-mandatory run with no answer adjudication is not counted complete."""
    tasks = [{"id": "t", "query": "q", "language": "en", "obligation": {"kind": "FACT", "mandatory": 1}}]
    runs = {
        "t": [
            {
                "goal_status": "COMPLETE",
                "mandatory_total": 1,
                "mandatory_satisfied": 1,
                # Legacy consistency proxy, present but must be ignored.
                "answer_expresses_mandatory": True,
                "consistency": "CONSISTENT",
                "evidence_findings": [],
                "latency": {"goal_end_to_end": 10},
                "provider_calls": 1,
                "fetched_documents": 1,
                "provider_errors": 0,
            }
        ]
    }
    report = build_report(tasks, runs, mode="offline", build="m1")
    assert report["aggregate"]["answer_completion_rate"] == 0.0
    assert report["aggregate"]["answer_completion_unadjudicated_runs"] == 1
    # An explicit adjudication supplied by a reviewer flips it.
    adjudicated = build_report(
        tasks, runs, mode="offline", build="m1", answer_adjudications={"m1:t:0": True}
    )
    assert adjudicated["aggregate"]["answer_completion_rate"] == 1.0
    assert adjudicated["aggregate"]["answer_completion_unadjudicated_runs"] == 0


def test_run_without_web_tool_is_not_a_zero_duration_sample():
    tasks = [{"id": "t", "query": "q", "language": "en", "obligation": {"kind": "FACT", "mandatory": 1}}]
    runs = {
        "t": [
            {"goal_status": "LIMITED", "mandatory_total": 1, "mandatory_satisfied": 0,
             "answer_completion_adjudicated": False, "evidence_findings": [],
             "latency": {"goal_end_to_end": 500}, "provider_calls": 0, "fetched_documents": 0,
             "provider_errors": 0},
            {"goal_status": "LIMITED", "mandatory_total": 1, "mandatory_satisfied": 0,
             "answer_completion_adjudicated": False, "evidence_findings": [],
             "latency": {"goal_end_to_end": 900, "tool:web_research": 400}, "provider_calls": 1,
             "fetched_documents": 1, "provider_errors": 0},
        ]
    }
    report = build_report(tasks, runs, mode="offline", build="test")
    # Only the run that actually invoked the tool contributes to the tool metric.
    stats = report["aggregate"]["latency"]["tool:web_research"]
    assert stats["n"] == 1
    assert stats["p50"] == 400
    assert report["aggregate"]["latency"]["goal_end_to_end"]["n"] == 2


def test_latency_observations_are_used_when_present():
    tasks = [{"id": "t", "query": "q", "language": "en", "obligation": {"kind": "FACT", "mandatory": 1}}]
    runs = {
        "t": [
            {"goal_status": "LIMITED", "mandatory_total": 1, "mandatory_satisfied": 0,
             "answer_completion_adjudicated": False, "evidence_findings": [],
             "latency": {"web:source:wikipedia": 300},
             "latency_observations": {"web:source:wikipedia": [100, 200], "goal_end_to_end": [1000]},
             "provider_calls": 2, "fetched_documents": 0, "provider_errors": 0},
        ]
    }
    report = build_report(tasks, runs, mode="offline", build="test")
    stats = report["aggregate"]["latency"]["web:source:wikipedia"]
    assert stats["n"] == 2
    assert stats["sample_kind"] == "provider_call"
    assert stats["p50"] == 150


def test_normalize_answer_adjudications_accepts_flat_and_wrapper():
    assert normalize_answer_adjudications({"m1:t:0": True}) == {"m1:t:0": True}
    wrapped = {"adjudications": {"m1:t:0": {"complete": False, "rationale": "source dump"}}}
    assert normalize_answer_adjudications(wrapped) == {"m1:t:0": False}


def test_offline_fixture_reproduction_matches_committed_report():
    """The committed offline fixture is reproducible from the committed runs."""
    root = Path(__file__).resolve().parent.parent
    tasks = json.loads((root / "tools/data/research_tasks.json").read_text(encoding="utf-8"))
    runs = json.loads((root / "tools/data/fixtures/offline_runs.json").read_text(encoding="utf-8"))
    report = build_report(tasks, runs, mode="offline", build="fixture")
    committed = json.loads((root / "docs/benchmarks/offline_fixture.json").read_text(encoding="utf-8"))
    assert report["aggregate"] == committed["aggregate"]


def test_offline_report_is_deterministic(tmp_path):
    first = build_report(TASKS, RUNS, mode="offline", build="m1")
    second = build_report(TASKS, RUNS, mode="offline", build="m1")
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_harness_never_writes_into_app(tmp_path):
    app_dir = Path(__file__).resolve().parent.parent / "app"
    before = {p for p in app_dir.rglob("*") if p.is_file()}
    tasks_file = tmp_path / "tasks.json"
    runs_file = tmp_path / "runs.json"
    tasks_file.write_text(json.dumps(TASKS))
    runs_file.write_text(json.dumps(RUNS))
    out = tmp_path / "report.json"
    main(
        [
            "--mode", "offline",
            "--tasks", str(tasks_file),
            "--runs", str(runs_file),
            "--build", "m1",
            "--out", str(out),
        ]
    )
    after = {p for p in app_dir.rglob("*") if p.is_file()}
    assert before == after
    assert out.exists()
    report = json.loads(out.read_text())
    assert report["mode"] == "offline"
