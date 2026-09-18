"""Before/after benchmark for the DeepSeek Flash runtime round.

Runs the *product* runtime with the new Flash configuration and records per-turn latency,
model-call counts, prompt sizes and completion tokens. The immutable baseline is the
committed v0.5 audit under ``docs/reviews/v05-runtime-performance/`` (external harness,
uncapped provider). This runner reuses that harness's observation wrappers but builds the
runtime with the new bounded configuration.

Usage:
    python3 docs/reviews/v06-deepseek-flash-latency/benchmark.py [scenario_id ...]
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

AUDIT = Path(__file__).resolve().parent
REPO = AUDIT.parents[2]
V05 = REPO / "docs" / "reviews" / "v05-runtime-performance"
sys.path.insert(0, str(V05))
sys.path.insert(0, str(REPO))

from harness import (ProfilingComposer, ProfilingInterpreter, ProfilingPageReader,  # noqa: E402
                     ProfilingPlanner, ProfilingSearchBackend,
                     ProfilingWeb, Recorder, instrument_runtime)


class FlashProfilingProvider:
    """Provider wrapper aware of the v0.6 bounded-call kwargs (bounds/deadline)."""

    def __init__(self, inner, purpose: str, recorder: Recorder) -> None:
        self._inner = inner
        self._purpose = purpose
        self._recorder = recorder

    def complete(self, prompt, *, model, timeout=30.0, max_tokens=None,
                 reasoning_effort=None, deadline=None, stream=None):
        start = time.perf_counter()
        prompt_chars = len(prompt or "")
        try:
            response = self._inner.complete(
                prompt, model=model, timeout=timeout, max_tokens=max_tokens,
                reasoning_effort=reasoning_effort, deadline=deadline, stream=stream)
        except BaseException as exc:  # noqa: BLE001
            self._recorder.record_llm(
                purpose=self._purpose, model=model, prompt_chars=prompt_chars,
                output_chars=0, usage={},
                duration_ms=(time.perf_counter() - start) * 1000.0, status="error",
                finish_reason=type(exc).__name__, fallback=True)
            raise
        usage = dict(getattr(response, "usage", {}) or {})
        self._recorder.record_llm(
            purpose=self._purpose, model=model, prompt_chars=prompt_chars,
            output_chars=len(getattr(response, "text", "") or ""), usage=usage,
            duration_ms=(time.perf_counter() - start) * 1000.0, status="ok",
            finish_reason=getattr(response, "finish_reason", "") or "")
        return response


def _wrap_live_web(recorder: Recorder):
    from app.tools.web_research import (BingSearch, DuckDuckGoLiteSearch, PageReader,
                                        WebResearchTool)

    backends = (
        ProfilingSearchBackend(DuckDuckGoLiteSearch(max_results=6), recorder, "duckduckgo-lite"),
        ProfilingSearchBackend(BingSearch(max_results=6), recorder, "bing"),
    )
    return ProfilingWeb(WebResearchTool(backends=backends,
                                        reader=ProfilingPageReader(PageReader(), recorder),
                                        fetch_pages=2, max_results=6), recorder)


def build_flash_runtime(recorder: Recorder):
    from app.artifact_runtime.factory import build_runtime
    from app.artifact_runtime.planner import LLMPlanner, LLMSemanticInterpreter
    from app.artifact_runtime.response import LLMResponseComposer
    from app.config import settings
    from app.knowledge.service import KnowledgeBase
    from app.knowledge.store import SqliteKnowledgeStore
    from app.llm.openai_provider import OpenAICompatibleProvider
    from app.persistence.store import SqliteOperationalStore

    run_dir = AUDIT / "_runtime"
    run_dir.mkdir(parents=True, exist_ok=True)
    knowledge_store = SqliteKnowledgeStore(settings.knowledge_store_path)
    knowledge = KnowledgeBase(knowledge_store)
    store = SqliteOperationalStore(run_dir / "operational.db")
    inner = OpenAICompatibleProvider(settings)
    timeout = max(settings.llm_request_timeout_seconds, 60.0)

    interpreter = ProfilingInterpreter(LLMSemanticInterpreter(
        FlashProfilingProvider(inner, "semantic", recorder), settings.llm_semantic_model,
        timeout=timeout, max_tokens=settings.llm_semantic_max_tokens,
        reasoning_effort=settings.llm_reasoning_effort,
        deadline=settings.llm_deadline_seconds), recorder)
    planner = ProfilingPlanner(LLMPlanner(
        FlashProfilingProvider(inner, "planning", recorder), settings.llm_planner_model,
        timeout=timeout, max_tokens=settings.llm_planner_max_tokens,
        reasoning_effort=settings.llm_reasoning_effort,
        deadline=settings.llm_deadline_seconds), recorder)
    composer = ProfilingComposer(LLMResponseComposer(
        FlashProfilingProvider(inner, "response", recorder), settings.llm_response_model,
        timeout=timeout, max_tokens=settings.llm_response_max_tokens,
        reasoning_effort=settings.llm_reasoning_effort,
        deadline=settings.llm_deadline_seconds), recorder)

    runtime = build_runtime(runtime_dir=str(run_dir), use_llm=True, knowledge=knowledge,
                            interpreter=interpreter, planner=planner, composer=composer,
                            web=_wrap_live_web(recorder), store=store,
                            today=lambda: __import__("datetime").date(2025, 9, 30))
    instrument_runtime(runtime, recorder)
    return runtime, knowledge_store, store


SCENARIOS = {
    "simple_local": {"messages": ["How many pitches were thrown in June 2021?"]},
    "analytical": {"messages": ["Which pitchers averaged the highest fastball velocity "
                                "in June 2021?"]},
    "multistep": {"messages": ["Which New York Yankees pitchers threw the most pitches "
                               "in June 2021?"]},
    "db_to_web": {"messages": ["Who threw the most pitches in June 2021, and is there "
                               "recent news about them?"]},
    "web_unavailable": {"messages": ["What is the latest news about MLB expansion?"]},
    "entity_ambiguity": {"messages": ["How did Luis pitch in 2021?"]},
    "impossible_future": {"messages": ["Who will win the 2030 World Series?"]},
    "contradictory": {"messages": ["Which players hit at least 100 home runs and at most "
                                   "5 home runs in 2021?"]},
    "followup": {"messages": ["How many pitches were thrown in June 2021?",
                              "What about July?"]},
    "clarification_resume": {"messages": [
        "How many pitches were thrown in June 2021?", "2"]},
    "long_conversation": {"messages": [
        "How many pitches were thrown in June 2021?",
        "How many pitches were thrown in July 2021?",
        "Which pitchers threw the most pitches in June 2021?",
        "Which pitchers threw the most pitches in July 2021?",
        "What was the average fastball velocity in June 2021?",
        "What was the average fastball velocity in July 2021?",
        "How many pitches were thrown in August 2021?",
        "Which pitchers threw the most pitches in August 2021?",
    ]},
}


def _meta(result) -> dict:
    coverage = result.coverage
    goal = result.trace.goal if result.trace else None
    latency = getattr(result.trace, "latency", {}) if result.trace else {}
    events = result.trace.events if result.trace else ()
    return {
        "coverage_verdict": getattr(coverage, "verdict", ""),
        "core_goal_supported": bool(getattr(coverage, "core_goal_supported", False)),
        "claims": len(result.claims),
        "artifact_count": len(result.artifacts),
        "goal_revision": getattr(goal, "revision", 0),
        "native_latency_ms": latency,
        "tool_calls": list(result.trace.tool_calls) if result.trace else [],
        "clarification_resumed": any(
            e.event_type == "CLARIFICATION_RESUMED" for e in events),
        "replan_skipped": any(e.event_type == "REPLAN_SKIPPED" for e in events),
    }


def run_scenario(name: str, spec: dict, recorder: Recorder) -> None:
    runtime, knowledge_store, store = build_flash_runtime(recorder)
    try:
        conversation = runtime.start_conversation()
        for turn, message in enumerate(spec["messages"], start=1):
            recorder.begin_turn(name, turn)
            started = time.perf_counter()
            conv = runtime.get_conversation(conversation)
            if turn > 1 and conv.pending_clarification is not None:
                result = runtime.respond_to_clarification(conversation, message)
            else:
                result = runtime.send_message(conversation, message)
            wall_ms = (time.perf_counter() - started) * 1000.0
            recorder.end_turn(result.status, len(result.answer),
                              meta={**_meta(result), "wall_ms": round(wall_ms, 2)})
            recorder.flush()
    finally:
        runtime.close()
        knowledge_store.close()
        store.close()


def main() -> int:
    requested = sys.argv[1:] or list(SCENARIOS)
    recorder = Recorder(AUDIT, secrets=())
    from app.config import settings

    print(f"[benchmark] model={settings.runtime_model} "
          f"reasoning={settings.llm_reasoning_effort} "
          f"deadline={settings.llm_deadline_seconds}s", flush=True)
    for name in requested:
        if name not in SCENARIOS:
            print(f"unknown scenario {name!r}")
            continue
        print(f"[benchmark] {name}", flush=True)
        try:
            run_scenario(name, SCENARIOS[name], recorder)
        except Exception as exc:  # noqa: BLE001
            recorder.error(f"scenario:{name}", exc)
            print(f"  ERROR {type(exc).__name__}: {exc}", flush=True)
        recorder.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
