"""External runtime-performance profiling harness (v0.5 audit).

MEASURE / TRACE / DIAGNOSE ONLY.

This module does **not** modify production code. It observes the v0.5 artifact runtime by
wrapping the *injected collaborators* and a small number of module-level engine functions
at instrumentation time. Every wrapper delegates to the real implementation; nothing is
short-circuited, cached, reordered or rewritten.

Design rules:

* monotonic, high-resolution ``time.perf_counter()`` only — never log ordering;
* record bounded/redacted summaries and sizes, never raw payloads with secrets;
* never persist model prompts, credentials or hidden chain-of-thought;
* exclusive leaf phases are marked so the critical path can be computed without double
  counting nested work.

The runtime is single-threaded and fully serial, so a serial sum of exclusive leaves plus
a residual orchestration term reproduces total latency.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# Leaf phases whose durations do not overlap each other and may therefore be summed to
# account for wall-clock time. Container/nested phases are recorded for the waterfall but
# excluded from the exclusive sum.
EXCLUSIVE_PHASES = (
    "resolve_entities",
    "semantic",
    "goal_construction",
    "planner_context",
    "planner_initial",
    "planner_next_action",
    "planner_add_needs",
    "binding_resolution",
    "tool_execute",
    "scope_verification",
    "artifact_registration",
    "judge_assess",
    "coverage_summarize",
    "claim_build",
    "claim_validate",
    "state_projection",
    "response_compose",
    "trace_build",
    "record_entities",
    "record_candidates",
    "persistence",
)

# Nested/structural phases measured for the waterfall only.
CONTAINER_PHASES = ("planner_loop", "turn", "execute_step")


def _bounded_text(value: Any, limit: int = 400) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _safe_summary(value: Any, *, limit: int = 400) -> Any:
    """A bounded, redacted, JSON-able summary of a possibly large value.

    Values themselves are never emitted verbatim beyond a bounded string; collections are
    reduced to their shape. This is deliberately conservative.
    """
    from app.tools.results import redact_secrets

    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        return redact_secrets(_bounded_text(value, limit), ())
    if isinstance(value, dict):
        keys = list(value.keys())
        return {"_shape": "object", "keys": keys[:20], "size": len(keys)}
    if isinstance(value, (list, tuple)):
        return {"_shape": type(value).__name__, "size": len(value)}
    return {"_shape": type(value).__name__}


@dataclass
class PhaseEvent:
    scenario: str
    turn: int
    phase: str
    start: float
    end: float
    start_rel: float
    detail: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        return (self.end - self.start) * 1000.0


class Recorder:
    """Collects redacted profiling rows for one audit process."""

    def __init__(self, out_dir: Path, secrets: tuple[str, ...] = ()) -> None:
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._secrets = tuple(item for item in secrets if item)
        self.profile: list[dict] = []
        self.llm_calls: list[dict] = []
        self.tool_calls: list[dict] = []
        self.timelines: list[dict] = []
        self.errors: list[dict] = []
        self.notes: list[dict] = []
        self.recovery: list[dict] = []

        # turn-scoping state
        self.scenario: str = ""
        self.turn: int = 0
        self._turn_start: float = 0.0
        self._events: list[PhaseEvent] = []
        self._turn_exclusive: dict[str, float] = {}
        self._nested_llm: int = 0
        self._nested_tools: int = 0

        # request-scoping state for tool timing attribution
        self.current_need_id: str = ""
        self.current_request_id: str = ""
        self._pending_tool: dict[str, dict] = {}

    # -- redaction ---------------------------------------------------------
    def redact(self, text: Any, limit: int = 400) -> str:
        from app.tools.results import redact_secrets

        return redact_secrets(_bounded_text(text, limit), self._secrets)

    # -- turn lifecycle ----------------------------------------------------
    def begin_turn(self, scenario: str, turn: int) -> None:
        self.scenario = scenario
        self.turn = turn
        self._turn_start = time.perf_counter()
        self._events = []
        self._turn_exclusive = {}
        self._nested_llm = 0
        self._nested_tools = 0

    def end_turn(self, status: str, answer_chars: int, meta: dict | None = None) -> None:
        end = time.perf_counter()
        total_ms = (end - self._turn_start) * 1000.0
        exclusive = {key: round(value * 1000.0, 3)
                     for key, value in self._turn_exclusive.items()}
        counted = sum(self._turn_exclusive.values()) * 1000.0
        residual = max(0.0, total_ms - counted)
        row = {
            "scenario": self.scenario,
            "turn": self.turn,
            "status": status,
            "answer_chars": answer_chars,
            "total_ms": round(total_ms, 3),
            "exclusive_ms": exclusive,
            "exclusive_sum_ms": round(counted, 3),
            "residual_orchestration_ms": round(residual, 3),
            "llm_calls": self._nested_llm,
            "tool_calls": self._nested_tools,
            "phases": [
                {
                    "phase": event.phase,
                    "start_rel_ms": round(event.start_rel * 1000.0, 3),
                    "duration_ms": round(event.duration_ms, 3),
                    "detail": event.detail,
                    "meta": event.meta,
                }
                for event in self._events
            ],
            **(meta or {}),
        }
        self.timelines.append(row)
        for event in self._events:
            self.profile.append({
                "scenario": self.scenario, "turn": self.turn, "phase": event.phase,
                "start_rel_ms": round(event.start_rel * 1000.0, 3),
                "duration_ms": round(event.duration_ms, 3),
                "detail": event.detail, "meta": event.meta,
            })

    @contextmanager
    def phase(self, phase: str, detail: str = "", meta: dict | None = None,
              exclusive: bool | None = None):
        start = time.perf_counter()
        start_rel = start - self._turn_start
        error = ""
        try:
            yield
        except BaseException as exc:  # noqa: BLE001 - record then re-raise unchanged
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            end = time.perf_counter()
            if exclusive is None:
                exclusive = phase in EXCLUSIVE_PHASES
            if exclusive:
                self._turn_exclusive[phase] = self._turn_exclusive.get(phase, 0.0) + \
                    (end - start)
            self._events.append(PhaseEvent(
                scenario=self.scenario, turn=self.turn, phase=phase, start=start, end=end,
                start_rel=start_rel,
                detail=self.redact(detail, 200) if not error else self.redact(error, 200),
                meta=meta or {}))

    # -- model calls -------------------------------------------------------
    def record_llm(self, *, purpose: str, model: str, prompt_chars: int,
                   output_chars: int, usage: dict, duration_ms: float, status: str,
                   finish_reason: str = "", fallback: bool = False) -> None:
        self._nested_llm += 1
        self.llm_calls.append({
            "scenario": self.scenario, "turn": self.turn, "purpose": purpose,
            "provider": "openai-compatible", "model": model,
            "prompt_chars": prompt_chars, "output_chars": output_chars,
            "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
            "cached_tokens": int(usage.get("prompt_cache_hit_tokens",
                                           usage.get("cached_tokens", 0)) or 0),
            "usage_available": bool(usage), "duration_ms": round(duration_ms, 3),
            "status": status, "finish_reason": finish_reason, "fallback": fallback,
        })

    # -- tool calls --------------------------------------------------------
    def begin_tool(self, *, request_id: str, capability: str, objective: str,
                   structured_inputs: dict, input_refs: tuple, expected_outputs: tuple) -> None:
        safe = {}
        for key in ("analytical_query", "op", "metric", "season", "start", "end",
                    "team", "query", "mentions", "focus", "entity_types", "names",
                    "direction", "field", "input_ref", "label"):
            if key in structured_inputs:
                value = structured_inputs[key]
                safe[key] = self.redact(json.dumps(value, default=str), 700) \
                    if isinstance(value, (dict, list, tuple)) else self.redact(value, 200)
        self._pending_tool[request_id] = {
            "request_id": request_id,
            "capability": capability,
            "objective": self.redact(objective, 160),
            "input_refs": list(input_refs),
            "input_ref_count": len(input_refs),
            "structured_input_keys": sorted(structured_inputs.keys()),
            "structured_inputs_safe": safe,
            "structured_inputs_summary": {
                key: _safe_summary(value) for key, value in structured_inputs.items()},
            "expected_outputs": list(expected_outputs),
        }

    def finalize_tool(self, *, conversation, goal, need, action) -> None:
        """Merge durable attempt facts and append the tool-call row.

        Called from the instrumented ``_execute`` *after* the engine has produced the
        durable ``ToolAttempt``, so the row carries the canonical outcome code, failure
        class, retryability and verified artifact/scopes rather than the tool's raw
        return value.
        """
        request_id = action.request.request_id
        row = self._pending_tool.pop(request_id, None)
        if row is None:
            row = {
                "request_id": request_id, "capability": action.request.capability,
                "objective": self.redact(action.request.objective, 160),
                "input_refs": list(action.request.input_refs),
                "input_ref_count": len(action.request.input_refs),
                "structured_input_keys": sorted(action.request.structured_inputs.keys()),
                "structured_inputs_summary": {
                    key: _safe_summary(value)
                    for key, value in action.request.structured_inputs.items()},
                "expected_outputs": list(action.request.expected_outputs),
            }
        bindings_need = need
        if need is not None and conversation is not None:
            bindings_need = next((item for item in conversation.needs
                                  if item.need_id == need.need_id), need)
        bindings = tuple(getattr(bindings_need, "input_bindings", ()) or ())
        attempt = next((item for item in conversation.attempts
                        if item.request_id == request_id), None)
        row.update({
            "scenario": self.scenario, "turn": self.turn,
            "need_id": getattr(need, "need_id", "") or "",
            "goal_revision": getattr(goal, "revision", 0),
            "need_status_after": getattr(bindings_need, "status", "") if need else "",
            "need_unsatisfied_inputs": list(
                getattr(bindings_need, "unsatisfied_inputs", ()) or ()),
            "bindings": [
                {"name": b.name, "export_type": b.export_type,
                 "source_export_id": b.source_export_id,
                 "source_need_id": b.source_need_id}
                for b in bindings],
            "outcome_code": getattr(attempt, "outcome_code", "") if attempt else "",
            "attempt_status": getattr(attempt, "status", "") if attempt else "",
            "failure_class": _failure_class(getattr(attempt, "outcome_code", ""))
            if attempt else "",
            "retryable": bool(getattr(attempt, "retryable", False)) if attempt else False,
            "detail": self.redact(getattr(attempt, "detail", "") if attempt else "", 300),
            "artifact_ids": list(getattr(attempt, "artifact_ids", ()) or ()) if attempt
            else [],
        })
        self.tool_calls.append(row)
        self._nested_tools += 1

    def annotate_tool_artifacts(self, *, request_id: str, artifacts: tuple) -> None:
        row = self._pending_tool.get(request_id)
        if row is None:
            return
        row["artifacts"] = [
            {
                "artifact_id": item.artifact_id, "kind": item.kind, "status": item.status,
                "export_types": [export.export_type for export in item.exports],
                "export_shapes": [_safe_summary(export.value) for export in item.exports],
                "text_chars": len(item.text_content or ""),
                "confidence": item.confidence,
                "declared_scope": _scope_summary(item.actual_scope),
                "requested_scope": _scope_summary(item.requested_scope),
                "receipt_keys": sorted((item.metadata or {}).get(
                    "execution_receipt", {}).keys()),
                "recovery_code": (item.metadata or {}).get("recovery_code", ""),
                "sql": self.redact(item.structured_data.get("sql", ""), 900)
                if isinstance(item.structured_data, dict) else "",
                "ir_digest": (item.structured_data.get("ir_digest", "")
                              if isinstance(item.structured_data, dict) else ""),
                "structured_keys": sorted(item.structured_data.keys())
                if isinstance(item.structured_data, dict) else [],
            }
            for item in artifacts
        ]

    def note_judge_for_need(self, *, conversation, need) -> None:
        """Attach the latest Judge assessment for a Need to its tool-call row."""
        if need is None:
            return
        assessment = next((item for item in reversed(conversation.assessments)
                           if getattr(item, "need_id", "") == need.need_id), None)
        if assessment is None:
            return
        for row in reversed(self.tool_calls):
            if row.get("scenario") == self.scenario and row.get("turn") == self.turn \
                    and row.get("need_id") == need.need_id:
                row["judge_verdict"] = assessment.verdict
                row["judge_outcome"] = assessment.judge_outcome
                row["judge_available"] = assessment.assessment_available
                row["judge_reasons"] = list(assessment.reasons)[:6]
                row["judge_gaps"] = list(assessment.gaps)[:6]
                row["supporting_artifact_ids"] = list(assessment.supporting_artifact_ids)
                return

    def annotate_verified_scope(self, *, conversation, action) -> None:
        """Merge verified scope dimensions into the matching tool-call row."""
        for row in reversed(self.tool_calls):
            if row.get("scenario") == self.scenario and row.get("turn") == self.turn \
                    and row.get("request_id") == action.request.request_id:
                verified = []
                for artifact_id in row.get("artifact_ids", ()):
                    artifact = conversation.artifacts.maybe(artifact_id)
                    if artifact is None:
                        continue
                    verified.extend(
                        {"artifact_id": artifact_id, "dimension": item.dimension,
                         "status": item.status,
                         "limitations": list(getattr(item, "limitations", ()) or ())}
                        for item in artifact.scope_verifications)
                    row["artifact_status"] = artifact.status
                row["verified_scope"] = verified
                row["hard_mismatch"] = any(
                    conversation.artifacts.maybe(artifact_id) is not None
                    and conversation.artifacts.maybe(artifact_id).has_hard_mismatch()
                    for artifact_id in row.get("artifact_ids", ()))
                return

    def note(self, event: str, **fields) -> None:
        self.notes.append({"scenario": self.scenario, "turn": self.turn,
                           "event": event, **fields})

    def record_recovery(self, **fields) -> None:
        self.recovery.append({"scenario": self.scenario, "turn": self.turn, **fields})

    def error(self, where: str, exc: BaseException) -> None:
        self.errors.append({"scenario": self.scenario, "turn": self.turn, "where": where,
                            "error": f"{type(exc).__name__}: {exc}"[:400]})

    # -- output ------------------------------------------------------------
    def write(self) -> None:
        self._write_jsonl("profile.jsonl", self.profile)
        self._write_jsonl("llm_calls.jsonl", self.llm_calls)
        self._write_jsonl("tool_calls.jsonl", self.tool_calls)
        self._write_jsonl("timelines.jsonl", self.timelines)
        self._write_jsonl("errors.jsonl", self.errors)
        self._write_jsonl("recovery.jsonl", self.recovery)
        self._write_jsonl("notes.jsonl", self.notes)

    def flush(self) -> None:
        """Persist collected rows and clear in-memory buffers (incremental safety)."""
        self.write()
        self.profile = []
        self.llm_calls = []
        self.tool_calls = []
        self.timelines = []
        self.errors = []
        self.recovery = []
        self.notes = []

    def _write_jsonl(self, name: str, rows: list[dict]) -> None:
        path = self.out_dir / name
        existing: list[dict] = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        existing.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        with path.open("w", encoding="utf-8") as handle:
            for row in [*existing, *rows]:
                handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _scope_summary(scope) -> dict:
    if scope is None:
        return {}
    return {
        "entities": list(scope.entities)[:12],
        "population": scope.population,
        "membership_basis": scope.membership_basis,
        "time_range": ([scope.time_range.start.isoformat(), scope.time_range.end.isoformat()]
                       if scope.time_range else None),
        "seasons": list(scope.seasons)[:12],
        "game_types": list(scope.game_types)[:12],
        "metric": scope.metric,
        "qualification": scope.qualification,
        "event_population": scope.event_population,
        "source_coverage": list(scope.source_coverage)[:8],
    }


# ---------------------------------------------------------------------------
# Collaborator wrappers (pure delegation + observation)
# ---------------------------------------------------------------------------


class ProfilingProvider:
    """Wraps a ModelProvider and attributes each call to a *purpose*."""

    def __init__(self, inner, purpose: str, recorder: Recorder) -> None:
        self._inner = inner
        self._purpose = purpose
        self._recorder = recorder

    def complete(self, prompt: str, *, model: str, timeout: float = 30.0):
        import os

        verbose = bool(os.getenv("AUDIT_VERBOSE"))
        start = time.perf_counter()
        prompt_chars = len(prompt or "")
        if verbose:
            print(f"  [llm] {self._purpose} start model={model} "
                  f"prompt_chars={prompt_chars} timeout={timeout}", flush=True)
        try:
            response = self._inner.complete(prompt, model=model, timeout=timeout)
        except BaseException as exc:  # noqa: BLE001 - observe, then re-raise unchanged
            self._recorder.record_llm(
                purpose=self._purpose, model=model, prompt_chars=prompt_chars,
                output_chars=0, usage={}, duration_ms=(time.perf_counter() - start) * 1000.0,
                status="error", finish_reason=type(exc).__name__, fallback=True)
            if verbose:
                print(f"  [llm] {self._purpose} ERROR {type(exc).__name__} "
                      f"after {time.perf_counter() - start:.1f}s", flush=True)
            raise
        usage = dict(getattr(response, "usage", {}) or {})
        self._recorder.record_llm(
            purpose=self._purpose, model=model, prompt_chars=prompt_chars,
            output_chars=len(getattr(response, "text", "") or ""), usage=usage,
            duration_ms=(time.perf_counter() - start) * 1000.0, status="ok",
            finish_reason=getattr(response, "finish_reason", "") or "")
        if verbose:
            print(f"  [llm] {self._purpose} done {time.perf_counter() - start:.1f}s "
                  f"out_chars={len(getattr(response, 'text', '') or '')} "
                  f"tokens(in={usage.get('prompt_tokens')}, "
                  f"out={usage.get('completion_tokens')})", flush=True)
        return response


class ProfilingInterpreter:
    def __init__(self, inner, recorder: Recorder) -> None:
        self._inner = inner
        self._recorder = recorder

    def brief(self, *, message: str, history: str, resolved_entities: tuple[str, ...],
              unknowns: tuple[str, ...], today: str):
        with self._recorder.phase(
                "semantic", detail=f"message_chars={len(message)} history_chars={len(history)}",
                meta={"history_chars": len(history), "resolved_entities": len(resolved_entities),
                      "unknowns": len(unknowns)}):
            return self._inner.brief(message=message, history=history,
                                     resolved_entities=resolved_entities,
                                     unknowns=unknowns, today=today)


class ProfilingPlanner:
    """Wraps a Planner. Records per-method latency and PlannerContext composition."""

    def __init__(self, inner, recorder: Recorder) -> None:
        self._inner = inner
        self._recorder = recorder

    def _context_meta(self, context) -> dict:
        try:
            caps = len(getattr(context, "capabilities", ()) or ())
            tables = len(getattr(context, "schema_tables", ()) or ())
            exports = len(getattr(context, "available_exports", ()) or ())
            feedback = getattr(context, "feedback", None)
            attempts = len(getattr(feedback, "attempts", ()) or ()) if feedback else 0
            render_cap = len(context.render_capabilities())
            render_schema = len(context.render_schema())
            render_exports = len(context.render_available_exports())
            render_feedback = len(context.render_feedback())
            return {
                "capability_views": caps, "schema_tables": tables,
                "available_exports": exports, "attempt_views": attempts,
                "chars_capabilities": render_cap, "chars_schema": render_schema,
                "chars_available_exports": render_exports,
                "chars_feedback": render_feedback,
                "chars_total_render": render_cap + render_schema + render_exports
                + render_feedback,
                "budget_remaining": getattr(context, "budget_remaining", None),
            }
        except Exception as exc:  # noqa: BLE001 - diagnostics must not alter behaviour
            self._recorder.error("planner_context_meta", exc)
            return {}

    def _prompt_chars(self, *, goal, brief, context) -> int:
        try:
            inner = self._inner
            prompt_fn = getattr(type(inner), "_prompt", None)
            if prompt_fn is not None:
                return len(prompt_fn(goal=goal, brief=brief, context=context))
        except Exception:  # noqa: BLE001
            pass
        return 0

    def initial_needs(self, *, goal, brief, context):
        meta = self._context_meta(context)
        meta["prompt_chars_estimated"] = self._prompt_chars(goal=goal, brief=brief,
                                                            context=context)
        with self._recorder.phase("planner_initial",
                                  detail=f"goal_rev={getattr(goal, 'revision', '?')}",
                                  meta=meta):
            return self._inner.initial_needs(goal=goal, brief=brief, context=context)

    def add_needs(self, *, goal, brief, existing, artifacts, gaps, context):
        meta = self._context_meta(context)
        meta["gaps"] = [self._recorder.redact(gap, 160) for gap in gaps][:8]
        meta["prompt_chars_estimated"] = self._prompt_chars(goal=goal, brief=brief,
                                                            context=context)
        with self._recorder.phase("planner_add_needs",
                                  detail=f"gaps={len(gaps)}", meta=meta):
            return self._inner.add_needs(goal=goal, brief=brief, existing=existing,
                                         artifacts=artifacts, gaps=gaps, context=context)

    def next_action(self, *, goal, needs, artifacts, context):
        with self._recorder.phase("planner_next_action",
                                  detail=f"needs={len(needs)} artifacts={len(artifacts)}"):
            return self._inner.next_action(goal=goal, needs=needs, artifacts=artifacts,
                                           context=context)


class ProfilingComposer:
    def __init__(self, inner, recorder: Recorder) -> None:
        self._inner = inner
        self._recorder = recorder

    def compose(self, *, message, goal, claims, artifacts, coverage, assumptions):
        with self._recorder.phase(
                "response_compose",
                detail=f"claims={len(claims)} artifacts={len(artifacts)}",
                meta={"claims": len(claims), "artifacts": len(artifacts),
                      "core_supported": bool(getattr(coverage, "core_goal_supported", False)),
                      "gaps": len(getattr(coverage, "gaps", ()) or ())}):
            return self._inner.compose(message=message, goal=goal, claims=claims,
                                       artifacts=artifacts, coverage=coverage,
                                       assumptions=assumptions)


class ProfilingStore:
    """Wraps an operational store; records payload size and write latency."""

    def __init__(self, inner, recorder: Recorder) -> None:
        self._inner = inner
        self._recorder = recorder

    def save_object(self, kind: str, object_id: str, run_id: str, payload: dict):
        start = time.perf_counter()
        result = self._inner.save_object(kind, object_id, run_id, payload)
        duration = (time.perf_counter() - start) * 1000.0
        self._recorder.note("store_save", kind=kind, payload_objects=len(payload),
                            payload_chars=len(json.dumps(payload, default=str)),
                            duration_ms=round(duration, 3))
        return result

    def get_object(self, kind: str, object_id: str):
        return self._inner.get_object(kind, object_id)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class ProfilingExecutor:
    """Wraps a DuckDB/PostgreSQL read-only executor to time database execution."""

    def __init__(self, inner, recorder: Recorder, label: str) -> None:
        self._inner = inner
        self._recorder = recorder
        self._label = label

    def execute_with_rows(self, sql: str):
        with self._recorder.phase(
                "db_execute", detail=f"{self._label}",
                meta={"sql_chars": len(sql or ""), "label": self._label},
                exclusive=False):
            return self._inner.execute_with_rows(sql)

    def execute(self, sql: str):
        return self._inner.execute(sql)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class ProfilingRosterProvider:
    def __init__(self, inner: Callable, recorder: Recorder) -> None:
        self._inner = inner
        self._recorder = recorder

    def __call__(self, team: str):
        with self._recorder.phase("roster_provider", detail=f"team_chars={len(team)}",
                                  exclusive=False):
            return self._inner(team)


class ProfilingWeb:
    """Wraps the live web research tool and its search/page seams."""

    def __init__(self, inner, recorder: Recorder) -> None:
        self._inner = inner
        self._recorder = recorder

    def research(self, query: str, *, fetch_pages: int | None = None):
        with self._recorder.phase(
                "web_research_total", detail=f"query_chars={len(query)}",
                meta={"fetch_pages": fetch_pages}, exclusive=False):
            return self._inner.research(query, fetch_pages=fetch_pages)

    def search(self, query: str):
        return self._inner.search(query)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class ProfilingSearchBackend:
    def __init__(self, inner, recorder: Recorder, label: str) -> None:
        self._inner = inner
        self._recorder = recorder
        self._label = label

    def search(self, query: str):
        with self._recorder.phase("web_search", detail=self._label,
                                  meta={"backend": self._label,
                                        "query_chars": len(query)}, exclusive=False):
            return self._inner.search(query)


class ProfilingPageReader:
    def __init__(self, inner, recorder: Recorder) -> None:
        self._inner = inner
        self._recorder = recorder

    def read(self, url: str):
        with self._recorder.phase("web_page_fetch", detail="page",
                                  meta={"url_host": _host_of(url)}, exclusive=False):
            return self._inner.read(url)


def _host_of(url: str) -> str:
    from urllib.parse import urlparse

    try:
        return urlparse(url).netloc
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------------------
# Instrumentation of one runtime instance
# ---------------------------------------------------------------------------


def instrument_runtime(runtime, recorder: Recorder) -> None:
    """Attach diagnostic wrappers to an already-constructed ArtifactRuntime.

    No production module is modified: only this instance's collaborators and a few
    engine module-level functions are patched, and only for the lifetime of the process.
    """
    global _ACTIVE_RECORDER
    _ACTIVE_RECORDER = recorder
    import app.artifact_runtime.engine as engine

    # -- engine module functions (name bindings imported by engine) ---------
    _wrap_module_function(engine, "resolve_bindings_with_gaps", recorder,
                          "binding_resolution")
    _wrap_module_function(engine, "verify_artifact_scope", recorder, "scope_verification")
    _wrap_module_function(engine, "build_claims", recorder, "claim_build")
    _wrap_module_function(engine, "validate_claims", recorder, "claim_validate")
    _wrap_module_function(engine, "obligation_coverage", recorder, "obligation_coverage",
                          exclusive=False)

    # -- analytical compile + SQL guard -----------------------------------
    try:
        import app.artifact_runtime.tools_analytics as analytics_module
        _wrap_compile(analytics_module, recorder)
    except Exception as exc:  # noqa: BLE001
        recorder.error("instrument_analytics", exc)
    try:
        import app.tools.execution as execution_module
        _wrap_module_function(execution_module, "guard_read_only_sql", recorder,
                              "sql_guard", exclusive=False)
    except Exception as exc:  # noqa: BLE001
        recorder.error("instrument_guard", exc)

    # -- runtime instance methods ------------------------------------------
    _wrap_method(runtime, "_planner_context", recorder, "planner_context")
    _wrap_method(runtime, "_add_artifacts", recorder, "artifact_registration")
    _wrap_method(runtime, "_build_trace", recorder, "trace_build")
    _wrap_method(runtime, "_resolve_entities", recorder, "resolve_entities")
    _wrap_method(runtime, "_new_goal", recorder, "goal_construction")
    _wrap_method(runtime, "_record_entities", recorder, "record_entities")
    _wrap_method(runtime, "_record_candidates", recorder, "record_candidates")
    _wrap_method(runtime, "_run_planner", recorder, "planner_loop", exclusive=False)
    _wrap_method(runtime, "_persist", recorder, "persistence")

    # _execute: set tool attribution context, finalize the tool-call row, and capture
    # verified scope/judge facts once the engine has completed the step.
    orig_execute = runtime._execute

    def _execute_wrapper(conversation, goal, need, action):
        recorder.current_need_id = action.need_id
        recorder.current_request_id = action.request.request_id
        try:
            result = orig_execute(conversation, goal, need, action)
        except BaseException as exc:  # noqa: BLE001
            recorder.error("execute", exc)
            raise
        else:
            try:
                recorder.finalize_tool(conversation=conversation, goal=goal, need=need,
                                       action=action)
                recorder.annotate_verified_scope(conversation=conversation,
                                                 action=action)
                recorder.note_judge_for_need(conversation=conversation, need=need)
            except Exception as exc:  # noqa: BLE001
                recorder.error("finalize_execute", exc)
            return result
        finally:
            recorder.current_need_id = ""
            recorder.current_request_id = ""

    runtime._execute = _execute_wrapper

    # -- judge -------------------------------------------------------------
    judge = runtime._judge
    if judge is not None:
        orig_assess = judge.assess_need

        def _assess_wrapper(goal, need, artifacts, **kwargs):
            with recorder.phase("judge_assess", detail=f"need={need.need_id}"):
                return orig_assess(goal, need, artifacts, **kwargs)

        judge.assess_need = _assess_wrapper
        orig_summarize = judge.summarize

        def _summarize_wrapper(goal, needs, assessments, **kwargs):
            with recorder.phase("coverage_summarize",
                                detail=f"needs={len(needs)} assessments={len(assessments)}"):
                return orig_summarize(goal, needs, assessments, **kwargs)

        judge.summarize = _summarize_wrapper

    # -- state projector ---------------------------------------------------
    projector = runtime._projector
    if projector is not None:
        _wrap_method(projector, "project_goal", recorder, "state_projection")
        _wrap_method(projector, "project_need", recorder, "state_projection")

    # -- tools -------------------------------------------------------------
    for tool in runtime._registry.all():
        _wrap_tool(tool, recorder)

    # -- executors ---------------------------------------------------------
    if getattr(runtime, "_parquet_executor", None) is not None:
        runtime._parquet_executor = ProfilingExecutor(runtime._parquet_executor, recorder,
                                                      "PARQUET")
    if getattr(runtime, "_postgres_executor", None) is not None:
        runtime._postgres_executor = ProfilingExecutor(runtime._postgres_executor, recorder,
                                                       "POSTGRES")

    # -- store -------------------------------------------------------------
    if getattr(runtime, "_store", None) is not None:
        runtime._store = ProfilingStore(runtime._store, recorder)

    # -- collaborators -----------------------------------------------------
    if getattr(runtime, "_interpreter", None) is not None and \
            not isinstance(runtime._interpreter, ProfilingInterpreter):
        runtime._interpreter = ProfilingInterpreter(runtime._interpreter, recorder)
    if getattr(runtime, "_planner", None) is not None and \
            not isinstance(runtime._planner, ProfilingPlanner):
        runtime._planner = ProfilingPlanner(runtime._planner, recorder)
    if getattr(runtime, "_composer", None) is not None and \
            not isinstance(runtime._composer, ProfilingComposer):
        runtime._composer = ProfilingComposer(runtime._composer, recorder)
    if getattr(runtime, "_web", None) is not None and \
            not isinstance(runtime._web, ProfilingWeb):
        runtime._web = ProfilingWeb(runtime._web, recorder)
    if getattr(runtime, "_roster_provider", None) is not None and \
            not isinstance(runtime._roster_provider, ProfilingRosterProvider):
        runtime._roster_provider = ProfilingRosterProvider(runtime._roster_provider,
                                                           recorder)

    # -- Shared Knowledge retrieval ---------------------------------------
    knowledge = getattr(runtime, "_knowledge", None)
    if knowledge is not None and hasattr(knowledge, "search") \
            and not getattr(knowledge.search, "_profiled", False):
        orig_search = knowledge.search

        def _knowledge_search(*args, **kwargs):
            with recorder.phase("knowledge_search",
                                detail=f"query_chars={len(str(args[0]) if args else '')}",
                                exclusive=False):
                return orig_search(*args, **kwargs)

        _knowledge_search._profiled = True  # type: ignore[attr-defined]
        knowledge.search = _knowledge_search

    # -- MLB StatsAPI people search (entity ambiguity path) ----------------
    lookup = getattr(runtime, "_entity_lookup", None)
    search = getattr(lookup, "_mlb_search", None)
    if search is not None and not getattr(search, "_profiled", False):
        def _search(name, _orig=search):
            with recorder.phase("mlb_registry_lookup",
                                detail=f"name_chars={len(str(name))}", exclusive=False):
                return _orig(name)

        _search._profiled = True  # type: ignore[attr-defined]
        lookup._mlb_search = _search


def _wrap_compile(module, recorder: Recorder) -> None:
    target = getattr(module, "compile_analytical_query", None)
    if target is None or getattr(target, "_profiled", False):
        return

    def wrapper(*args, **kwargs):
        rec = _ACTIVE_RECORDER
        if rec is None:
            return target(*args, **kwargs)
        with rec.phase("ir_compile"):
            result = target(*args, **kwargs)
        try:
            rec.note("ir_compile", ok=bool(getattr(result, "ok", False)),
                     code=str(getattr(result, "code", "")),
                     digest=str(getattr(result, "ir_digest", "")),
                     sql_chars=len(getattr(result, "sql", "") or ""))
        except Exception:  # noqa: BLE001
            pass
        return result

    wrapper._profiled = True  # type: ignore[attr-defined]
    wrapper._original = target  # type: ignore[attr-defined]
    setattr(module, "compile_analytical_query", wrapper)


_ACTIVE_RECORDER: "Recorder | None" = None


def _wrap_module_function(module, name: str, recorder: Recorder, phase: str,
                          exclusive: bool = True) -> None:
    target = getattr(module, name, None)
    if target is None or getattr(target, "_profiled", False):
        return

    def wrapper(*args, **kwargs):
        rec = _ACTIVE_RECORDER
        if rec is None:
            return target(*args, **kwargs)
        with rec.phase(phase, exclusive=exclusive):
            return target(*args, **kwargs)

    wrapper._profiled = True  # type: ignore[attr-defined]
    wrapper._original = target  # type: ignore[attr-defined]
    setattr(module, name, wrapper)


def _wrap_method(obj, name: str, recorder: Recorder, phase: str,
                 exclusive: bool = True) -> None:
    target = getattr(obj, name, None)
    if target is None or getattr(target, "_profiled", False):
        return

    def wrapper(*args, **kwargs):
        with recorder.phase(phase, exclusive=exclusive):
            return target(*args, **kwargs)

    wrapper._profiled = True  # type: ignore[attr-defined]
    wrapper._original = target  # type: ignore[attr-defined]
    setattr(obj, name, wrapper)


def _wrap_tool(tool, recorder: Recorder) -> None:
    original = tool.run
    if getattr(original, "_profiled", False):
        return

    def run(request, context):
        recorder.begin_tool(
            request_id=request.request_id, capability=request.capability,
            objective=request.objective, structured_inputs=dict(request.structured_inputs),
            input_refs=tuple(request.input_refs),
            expected_outputs=tuple(request.expected_outputs))
        start = time.perf_counter()
        try:
            with recorder.phase("tool_execute", detail=request.capability,
                                meta={"capability": request.capability}):
                outcome = original(request, context)
        except BaseException as exc:  # noqa: BLE001 - central runtime catches too
            recorder.error("tool_run", exc)
            raise
        duration = (time.perf_counter() - start) * 1000.0
        recorder.annotate_tool_artifacts(request_id=request.request_id,
                                         artifacts=tuple(getattr(outcome, "artifacts", ()) or ()))
        pending = recorder._pending_tool.get(request.request_id)
        if pending is not None:
            pending["tool_duration_ms"] = round(duration, 3)
            pending["raw_recovery_code"] = str(getattr(outcome, "recovery_code", "") or "")
            pending["raw_detail"] = recorder.redact(
                str(getattr(outcome, "detail", "") or ""), 300)
            pending["applied_fields"] = list(getattr(outcome, "applied_fields", ()) or ())
            pending["referenced_exports"] = list(
                getattr(outcome, "referenced_exports", ()) or ())
        return outcome

    run._profiled = True  # type: ignore[attr-defined]
    tool.run = run


def record_attempt_from_conversation(recorder: Recorder, conversation, request_id: str,
                                     goal_revision: int, bindings: tuple) -> None:
    """Merge durable attempt facts into the tool-call row after ``_execute`` returns."""
    row = recorder._pending_tool.get(request_id)
    if row is None:
        return
    for attempt in conversation.attempts:
        if attempt.request_id != request_id:
            continue
        row["need_id"] = attempt.need_id
        row["goal_revision"] = goal_revision
        row["outcome_code"] = attempt.outcome_code
        row["failure_class"] = _failure_class(attempt.outcome_code)
        row["retryable"] = attempt.retryable
        row["detail"] = recorder.redact(attempt.detail, 300)
        row["attempt_status"] = attempt.status
        break
    if bindings:
        row["bindings"] = [
            {"name": b.name, "export_type": b.export_type,
             "source_export_id": b.source_export_id, "source_need_id": b.source_need_id}
            for b in bindings]


def _failure_class(code: str) -> str:
    try:
        from app.artifact_runtime.recovery import failure_class

        return failure_class(code)
    except Exception:  # noqa: BLE001
        return ""
