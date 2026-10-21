#!/usr/bin/env python3
"""Web Research benchmark harness (M1-T3 repair, ADR 0024/0027/0028).

This tool is **not** on the runtime path. It scores frozen representative tasks for
Answer Completion Rate (ACR), Evidence Precision (EP), Information Gain (IG) and stage
latency, and compares an old build against the M1 build.

Two modes:

- ``offline``: score committed fixture runs deterministically (regression only, no network).
- ``live``: run the real Runtime (``Engine.ask``) per task and collect the persisted
  artifacts/diagnostics. Live results depend on the date and on external source availability.

Measurement contract (M1-T3 repair):

- Aggregate latency percentiles are computed from the **underlying run observations**, never
  from a percentile of per-task percentiles. Every latency metric reports its raw sample
  count ``n`` plus ``p50``/``p95``/``min``/``max`` and a ``sample_kind`` that distinguishes a
  **goal run** (``goal_end_to_end``), a **tool invocation** (``tool:web_research``), a
  **provider call** (``web:source:*``) and a plain **stage observation**. A run that did not
  invoke ``web_research`` contributes no zero-duration sample.
- Answer Completion Rate requires an **explicit answer adjudication** of whether the final
  user-facing answer expresses every mandatory result. It is never derived from
  ``consistency == CONSISTENT`` or ``GoalStatus == COMPLETE``. An unadjudicated run is counted
  and reported separately; it can never be silently treated as complete.
- Evidence Precision keeps the adjudication set stable and reports the explicit
  ``supported / adjudicated`` and ``false_support / adjudicated`` denominators.

Reports are machine-readable JSON. ``build_report`` is a pure function with no timestamp so
the same inputs always produce byte-identical output; the CLI adds ``generated_at``.

The harness never writes into ``app/``; it only writes the ``--out`` report path.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

# The harness is invoked as a script; make the project root importable without installing.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

STAGES = (
    "web:source:*",
    "web:rank",
    "web:cache",
    "web:search",
    "web:fetch",
    "web:verify",
    "tool:web_research",
    "goal_end_to_end",
)


def percentile(values: list[float], p: float) -> float:
    """Linear-interpolated percentile (0..100) of a value list; 0 for an empty list."""
    numbers = sorted(float(v) for v in values)
    if not numbers:
        return 0.0
    if len(numbers) == 1:
        return numbers[0]
    rank = (p / 100.0) * (len(numbers) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(numbers) - 1)
    fraction = rank - lower
    return round(numbers[lower] + fraction * (numbers[upper] - numbers[lower]), 3)


def sample_kind(stage: str) -> str:
    """Classify a latency stage for the sample-count contract."""
    if stage == "goal_end_to_end":
        return "goal_run"
    if stage == "tool:web_research":
        return "tool_invocation"
    if stage.startswith("web:source:"):
        return "provider_call"
    return "stage_observation"


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_tasks(path: str | Path) -> list[dict[str, Any]]:
    tasks = load_json(path)
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("task file must contain a non-empty list")
    for task in tasks:
        for key in ("id", "query", "language", "obligation"):
            if key not in task:
                raise ValueError(f"task is missing required key '{key}': {task!r}")
    return tasks


def load_runs(path: str | Path) -> dict[str, list[dict[str, Any]]]:
    runs = load_json(path)
    if not isinstance(runs, dict):
        raise ValueError("runs file must be a mapping of task id to a list of runs")
    return runs


def normalize_answer_adjudications(raw: Any) -> dict[str, bool]:
    """Accept either a flat ``{run_key: bool}`` map or a documented wrapper.

    The documented wrapper is ``{"adjudications": {run_key: {"complete": bool, ...}}}``
    so a rationale can live next to the judgement. Only the boolean is consumed.
    """
    if not isinstance(raw, dict):
        return {}
    entries = raw.get("adjudications") if isinstance(raw.get("adjudications"), dict) else raw
    normalized: dict[str, bool] = {}
    for key, value in entries.items():
        if isinstance(value, bool):
            normalized[key] = value
        elif isinstance(value, dict) and "complete" in value:
            normalized[key] = bool(value["complete"])
    return normalized


def run_key(build: str, task_id: str, index: int) -> str:
    """Stable identity for one run; used to key answer-completion adjudications."""
    return f"{build}:{task_id}:{index}"


def _answer_adjudication(
    run: dict[str, Any],
    build: str,
    task_id: str,
    index: int,
    adjudications: dict[str, bool],
) -> bool | None:
    """The explicit answer-completion judgement for one run, or ``None`` if unadjudicated.

    The committed live runs carry an ``answer_expresses_mandatory`` field that older harness
    versions set from consistency status. That proxy is never trusted here; only an explicit
    adjudication map (or a run's own explicit ``answer_completion_adjudicated`` label, used by
    the deterministic offline fixture) can mark a run complete.
    """
    if run_key(build, task_id, index) in adjudications:
        return bool(adjudications[run_key(build, task_id, index)])
    if "answer_completion_adjudicated" in run:
        value = run.get("answer_completion_adjudicated")
        return None if value is None else bool(value)
    return None


def _is_complete(
    run: dict[str, Any],
    build: str,
    task_id: str,
    index: int,
    adjudications: dict[str, bool],
) -> bool:
    mandatory_total = int(run.get("mandatory_total") or 0)
    mandatory_satisfied = int(run.get("mandatory_satisfied") or 0)
    if not (
        run.get("goal_status") == "COMPLETE"
        and mandatory_total > 0
        and mandatory_satisfied >= mandatory_total
    ):
        return False
    return _answer_adjudication(run, build, task_id, index, adjudications) is True


def _finding_truth(finding: dict[str, Any], adjudications: dict[str, bool]) -> bool | None:
    genuine = finding.get("genuine")
    if genuine is not None:
        return bool(genuine)
    finding_id = finding.get("id")
    if finding_id is not None and finding_id in adjudications:
        return bool(adjudications[finding_id])
    return None


def _observations(run: dict[str, Any]) -> dict[str, list[float]]:
    """Per-run latency observations.

    New runs carry ``latency_observations`` (one value per actually-recorded span). Older
    committed runs only carry the per-run summed ``latency``; each present stage then
    contributes exactly one sample. A stage absent from a run contributes **no** sample, so a
    run without ``web_research`` never injects a zero.
    """
    values: dict[str, list[float]] = {}
    observations = run.get("latency_observations")
    if isinstance(observations, dict) and observations:
        for stage, items in observations.items():
            for item in items if isinstance(items, list) else [items]:
                try:
                    values.setdefault(stage, []).append(float(item))
                except (TypeError, ValueError):
                    continue
        return values
    for stage, ms in (run.get("latency") or {}).items():
        try:
            values.setdefault(stage, []).append(float(ms))
        except (TypeError, ValueError):
            continue
    return values


def _latency_stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "p50": 0.0, "p95": 0.0, "min": 0.0, "max": 0.0}
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "p50": percentile(ordered, 50),
        "p95": percentile(ordered, 95),
        "min": round(ordered[0], 3),
        "max": round(ordered[-1], 3),
    }


def _score_task(
    task: dict[str, Any],
    runs: list[dict[str, Any]],
    adjudications: dict[str, bool],
    answer_adjudications: dict[str, bool],
    *,
    build: str,
) -> dict[str, Any]:
    latency_values: dict[str, list[float]] = {}
    provider_calls = 0
    fetched_documents = 0
    provider_errors = 0
    information_gain: dict[str, float] = {}
    findings: dict[str, dict[str, Any]] = {}
    answer_complete = False
    adjudicated_runs = 0
    complete_runs = 0
    for index, run in enumerate(runs):
        adjudication = _answer_adjudication(run, build, task["id"], index, answer_adjudications)
        if adjudication is not None:
            adjudicated_runs += 1
        if _is_complete(run, build, task["id"], index, answer_adjudications):
            answer_complete = True
            complete_runs += 1
        provider_calls += int(run.get("provider_calls") or 0)
        fetched_documents += int(run.get("fetched_documents") or 0)
        provider_errors += int(run.get("provider_errors") or 0)
        for stage, items in _observations(run).items():
            latency_values.setdefault(stage, []).extend(items)
        for key, value in (run.get("information_gain") or {}).items():
            if isinstance(value, (int, float)):
                information_gain[key] = information_gain.get(key, 0) + float(value)
        for finding in run.get("evidence_findings") or []:
            if finding.get("id"):
                findings[finding["id"]] = finding

    supported = [f for f in findings.values() if f.get("status") == "SUPPORTED"]
    genuine = [f for f in supported if _finding_truth(f, adjudications) is True]
    adjudicated_supported = [f for f in supported if _finding_truth(f, adjudications) is not None]
    unadjudicated = [f for f in supported if _finding_truth(f, adjudications) is None]
    false_support = len(supported) - len(genuine)
    ep = (len(genuine) / len(supported)) if supported else None
    false_rate = (false_support / len(supported)) if supported else None

    latency_stats = {stage: _latency_stats(items) for stage, items in sorted(latency_values.items())}
    return {
        "task_id": task["id"],
        "category": task.get("category", ""),
        "answer_completion": 1 if answer_complete else 0,
        "answer_completion_runs": len(runs),
        "answer_completion_complete_runs": complete_runs,
        "answer_completion_adjudicated_runs": adjudicated_runs,
        "answer_completion_unadjudicated_runs": max(0, len(runs) - adjudicated_runs),
        "evidence_precision": round(ep, 4) if ep is not None else None,
        "evidence_precision_numerator": len(genuine),
        "evidence_precision_denominator": len(supported),
        "evidence_precision_adjudicated": len(adjudicated_supported),
        "evidence_precision_false_support": false_support,
        "evidence_precision_false_support_rate": round(false_rate, 4) if false_rate is not None else None,
        "evidence_precision_unadjudicated": len(unadjudicated),
        "latency": latency_stats,
        "latency_p50": {stage: stats["p50"] for stage, stats in latency_stats.items()},
        "latency_p95": {stage: stats["p95"] for stage, stats in latency_stats.items()},
        "provider_calls": provider_calls,
        "fetched_documents": fetched_documents,
        "provider_errors": provider_errors,
        "information_gain": {key: round(value, 4) for key, value in sorted(information_gain.items())},
        "runs": len(runs),
    }


def _aggregate(
    task_reports: list[dict[str, Any]],
    runs: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    task_count = len(task_reports)
    completed = sum(report["answer_completion"] for report in task_reports)
    supported = sum(report["evidence_precision_denominator"] for report in task_reports)
    genuine = sum(report["evidence_precision_numerator"] for report in task_reports)
    adjudicated = sum(report["evidence_precision_adjudicated"] for report in task_reports)
    unadjudicated = sum(report["evidence_precision_unadjudicated"] for report in task_reports)
    false_support = supported - genuine
    information_gain: dict[str, float] = {}
    for report in task_reports:
        for key, value in report["information_gain"].items():
            information_gain[key] = information_gain.get(key, 0) + value

    # Aggregate latency from the underlying run observations, not from task percentiles.
    pooled: dict[str, list[float]] = {}
    for task_id, task_runs in runs.items():
        for run in task_runs:
            for stage, items in _observations(run).items():
                pooled.setdefault(stage, []).extend(items)
    latency = {
        stage: {**_latency_stats(items), "sample_kind": sample_kind(stage)}
        for stage, items in sorted(pooled.items())
    }
    return {
        "task_count": task_count,
        "answer_completion_rate": round(completed / task_count, 4) if task_count else 0.0,
        "answer_completion_numerator": completed,
        "answer_completion_denominator": task_count,
        "answer_completion_adjudicated_runs": sum(r["answer_completion_adjudicated_runs"] for r in task_reports),
        "answer_completion_unadjudicated_runs": sum(r["answer_completion_unadjudicated_runs"] for r in task_reports),
        "evidence_precision": round(genuine / supported, 4) if supported else None,
        "evidence_precision_numerator": genuine,
        "evidence_precision_denominator": supported,
        "evidence_precision_adjudicated": adjudicated,
        "evidence_precision_false_support": false_support,
        "evidence_precision_false_support_rate": round(false_support / supported, 4) if supported else None,
        "evidence_precision_unadjudicated": unadjudicated,
        "latency": latency,
        "latency_p50": {stage: stats["p50"] for stage, stats in latency.items()},
        "latency_p95": {stage: stats["p95"] for stage, stats in latency.items()},
        "provider_calls": sum(report["provider_calls"] for report in task_reports),
        "fetched_documents": sum(report["fetched_documents"] for report in task_reports),
        "provider_errors": sum(report["provider_errors"] for report in task_reports),
        "information_gain": {key: round(value, 4) for key, value in sorted(information_gain.items())},
    }


def build_report(
    tasks: list[dict[str, Any]],
    runs: dict[str, list[dict[str, Any]]],
    *,
    mode: str,
    build: str,
    adjudications: dict[str, bool] | None = None,
    answer_adjudications: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Build a deterministic report (no timestamps) from tasks and per-task runs."""
    adjudications = adjudications or {}
    answer_adjudications = answer_adjudications or {}
    task_reports = [
        _score_task(task, runs.get(task["id"], []), adjudications, answer_adjudications, build=build)
        for task in tasks
    ]
    return {
        "mode": mode,
        "build": build,
        "stages": list(STAGES),
        "tasks": task_reports,
        "aggregate": _aggregate(task_reports, runs),
        "runs": runs,
    }


# --------------------------------------------------------------------------- live mode


def _read_latency(config, session_id: str, goal_id: str) -> tuple[dict[str, int], dict[str, list[int]]]:
    """Return the per-run summed latency plus the raw per-span observations.

    ``latency_observations`` records every recorded span (including every provider call), so
    the aggregate can report true sample counts. ``latency`` keeps the summed per-stage value
    for backward compatibility with earlier reports.
    """
    from app.observe.diagnostics import diagnostic_path

    path = diagnostic_path(config.runtime_dir, session_id, goal_id)
    if not path.exists():
        return {}, {}
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, {}
    latency: dict[str, int] = {}
    observations: dict[str, list[int]] = {}
    for record in records:
        if record.get("stage") != "latency":
            continue
        data = record.get("data", {})
        for span in data.get("spans", []):
            stage = span.get("stage", "")
            ms = int(span.get("ms") or 0)
            latency[stage] = latency.get(stage, 0) + ms
            observations.setdefault(stage, []).append(ms)
        # The Goal end-to-end clock is a tracker summary, not a recorded span.
        end_to_end = data.get("summary", {}).get("end_to_end")
        if end_to_end is not None:
            latency["goal_end_to_end"] = int(end_to_end)
            observations.setdefault("goal_end_to_end", []).append(int(end_to_end))
    return latency, observations


def collect_live_runs(tasks: list[dict[str, Any]], *, session_prefix: str, repeat: int = 1) -> dict[str, list[dict[str, Any]]]:
    from app.config import get_config
    from app.runtime.engine import Engine

    config = get_config()
    engine = Engine(config)
    runs: dict[str, list[dict[str, Any]]] = {}
    for task in tasks:
        for index in range(repeat):
            session_id = f"{session_prefix}_{task['id']}_{index}"
            result = engine.ask(session_id, task["query"], trace_level="off")
            latency, observations = _read_latency(config, session_id, result.goal.id)
            runs.setdefault(task["id"], []).append(
                _live_run_from_result(engine, task, result, latency, observations)
            )
    return runs


def _live_run_from_result(engine, task, result, latency, observations=None):
    """Reuse _live_run's scoring but on an already-executed Result (avoids a second call)."""
    from app.verification.evidence import VerificationStatus

    web = [a for a in result.artifacts if a.kind == "WEB_EVIDENCE"]
    findings: list[dict[str, Any]] = []
    provider_calls = 0
    fetched_documents = 0
    provider_errors = 0
    span_text: dict[str, str] = {}
    fetch_attempts: list[dict[str, Any]] = []
    source_outcomes: list[dict[str, Any]] = []
    document_provenance: list[dict[str, Any]] = []
    tavily_usage = {"search_calls": 0, "extract_calls": 0, "reported_credits": 0.0}
    source_intents: list[str] = []
    broad_search_used = 0
    documents_by_source_type: dict[str, int] = {}
    proposition_statements = {p.id: p.statement for p in result.projection.propositions}
    for artifact in web:
        for span in artifact.structured_data.get("evidence_spans", []):
            span_text[span.get("id")] = span.get("text", "")
    for artifact in web:
        data = artifact.structured_data or {}
        fetch_attempts.extend(data.get("fetch_attempts") or [])
        source_outcomes.extend(data.get("source_outcomes") or [])
        if data.get("source_intent"):
            source_intents.append(str(data["source_intent"]))
        broad_search_used += int(bool((data.get("broad_search") or {}).get("used")))
        for document in data.get("documents") or []:
            source_type = str(document.get("source_type") or "UNKNOWN")
            documents_by_source_type[source_type] = documents_by_source_type.get(source_type, 0) + 1
            document_provenance.append({key: document.get(key) for key in (
                "id", "url", "final_url", "canonical_url", "provider", "retrieval_status",
                "retrieval_method", "representation_provider", "retrieved_at", "content_hash",
                "body_hash", "extractor_version", "source_language", "source_type",
            )})
        for finding in data.get("evidence_findings", []):
            findings.append(
                {
                    "id": finding.get("id"),
                    "status": finding.get("status"),
                    "document_id": finding.get("document_id"),
                    "span_id": finding.get("span_id"),
                    "proposition": proposition_statements.get(finding.get("proposition_id"), ""),
                    "span_text": (span_text.get(finding.get("span_id"), "") or "")[:500],
                    "genuine": finding.get("genuine"),
                }
            )
        metrics = data.get("research_metrics") or {}
        tavily_usage["search_calls"] += int(metrics.get("tavily_search_calls") or 0)
        tavily_usage["extract_calls"] += int(metrics.get("tavily_extract_calls") or 0)
        tavily_usage["reported_credits"] += float(metrics.get("tavily_reported_credits") or 0)
        provider_calls += int(metrics.get("provider_calls") or 0)
        fetched_documents += len(data.get("documents") or [])
        provider_errors += sum(
            1 for outcome in data.get("source_outcomes", []) if outcome.get("status") not in {"OK", "EMPTY"}
        )
    mandatory = [p for p in result.projection.propositions if p.mandatory]
    satisfied = sum(1 for p in mandatory if getattr(p.coverage, "value", p.coverage) == "SATISFIED")
    web_supported = sum(1 for finding in findings if finding.get("status") == VerificationStatus.SUPPORTED.value)
    moved = sum(
        1
        for p in mandatory
        if getattr(p.coverage, "value", p.coverage) == "SATISFIED" and p.evidence_finding_ids
    )
    return {
        "goal_status": getattr(result.projection.goal_status, "value", str(result.projection.goal_status)),
        "mandatory_total": len(mandatory),
        "mandatory_satisfied": satisfied,
        # The final answer text is captured so a human/reviewer can adjudicate answer
        # completion explicitly. It is never used to auto-derive completion.
        "answer": (result.answer or "")[:4000],
        "answer_completion_adjudicated": None,
        "evidence_findings": findings,
        "latency": latency,
        "latency_observations": observations or {},
        "provider_calls": provider_calls,
        "fetched_documents": fetched_documents,
        "provider_errors": provider_errors,
        "fetch_attempts": fetch_attempts,
        "source_outcomes": source_outcomes,
        "document_provenance": document_provenance,
        "tavily_usage": tavily_usage,
        "source_intents": source_intents,
        "broad_search_used": broad_search_used,
        "documents_by_source_type": documents_by_source_type,
        "information_gain": {"mandatory_propositions_moved": moved, "verified_facts": web_supported},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Web Research benchmark harness (M1-T3)")
    parser.add_argument("--mode", choices=["offline", "live"], required=True)
    parser.add_argument("--tasks", required=True, help="frozen task set JSON")
    parser.add_argument("--runs", help="offline runs JSON (required for --mode offline)")
    parser.add_argument("--adjudications", help="optional finding-id -> genuine map")
    parser.add_argument("--answer-adjudications", help="optional run-key -> answer-complete map")
    parser.add_argument("--build", default="current", help="build label (e.g. old=7ad65b2, m1)")
    parser.add_argument("--out", required=True, help="output report path (never inside app/)")
    parser.add_argument("--session", default="bench", help="session prefix for live runs")
    parser.add_argument("--repeat", type=int, default=1, help="live repeats per task")
    args = parser.parse_args(argv)

    tasks = load_tasks(args.tasks)
    if args.mode == "offline":
        if not args.runs:
            parser.error("--runs is required for offline mode")
        runs = load_runs(args.runs)
    else:
        runs = collect_live_runs(tasks, session_prefix=args.session, repeat=max(1, args.repeat))
    adjudications = load_json(args.adjudications) if args.adjudications else {}
    answer_adjudications = normalize_answer_adjudications(load_json(args.answer_adjudications)) if args.answer_adjudications else {}
    report = build_report(
        tasks,
        runs,
        mode=args.mode,
        build=args.build,
        adjudications=adjudications,
        answer_adjudications=answer_adjudications,
    )
    report["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report["aggregate"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
