"""Builders that compose a *real* artifact runtime and attach the diagnostic harness.

No production module is edited. The runtime is built by the production composition root
(``app.artifact_runtime.factory.build_runtime``) and then observed via
``harness.instrument_runtime``.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from app.config import settings
from app.knowledge.service import KnowledgeBase
from app.knowledge.store import SqliteKnowledgeStore
from app.persistence.store import SqliteOperationalStore

from harness import (ProfilingComposer, ProfilingInterpreter, ProfilingPageReader,
                     ProfilingPlanner, ProfilingProvider, ProfilingRosterProvider,
                     ProfilingSearchBackend, ProfilingWeb, Recorder, instrument_runtime)

TODAY = date(2025, 9, 30)
AUDIT_ROOT = Path(__file__).resolve().parent


def _wrap_live_web(recorder: Recorder):
    from app.tools.web_research import (BingSearch, DuckDuckGoLiteSearch, PageReader,
                                        WebResearchTool as LiveWebResearchTool)

    backends = (
        ProfilingSearchBackend(DuckDuckGoLiteSearch(max_results=6), recorder, "duckduckgo-lite"),
        ProfilingSearchBackend(BingSearch(max_results=6), recorder, "bing"),
    )
    reader = ProfilingPageReader(PageReader(), recorder)
    inner = LiveWebResearchTool(backends=backends, reader=reader, fetch_pages=2,
                                max_results=6)
    return ProfilingWeb(inner, recorder)


def build_offline(*, recorder: Recorder, needs=(), interpreter=None, web=None,
                  roster_provider=None, batting=None, planner=None,
                  runtime_dir: Path | None = None, use_store: bool = True,
                  pending_question: str = "", pending_options=()):
    """Offline, deterministic runtime with a scripted planner."""
    from app.artifact_runtime.factory import build_runtime
    from app.artifact_runtime.planner import ScriptedPlanner
    from scenarios import FixedInterpreter

    knowledge_store = SqliteKnowledgeStore(settings.knowledge_store_path)
    knowledge = KnowledgeBase(knowledge_store)
    store = None
    store_dir = Path(runtime_dir) if runtime_dir else (AUDIT_ROOT / "_runtime")
    if use_store:
        store_dir.mkdir(parents=True, exist_ok=True)
        store = SqliteOperationalStore(store_dir / "operational.db")

    if web is None:
        web = _wrap_live_web(recorder)

    runtime = build_runtime(
        runtime_dir=str(store_dir) if use_store else str(AUDIT_ROOT / "_runtime_nostore"),
        use_llm=False, knowledge=knowledge,
        interpreter=interpreter or FixedInterpreter(question=pending_question,
                                                    options=pending_options),
        planner=planner or ScriptedPlanner(tuple(needs)),
        web=web, roster_provider=roster_provider, batting=batting,
        store=store, today=lambda: TODAY)
    instrument_runtime(runtime, recorder)
    return runtime, knowledge_store, store


def _capping_provider(inner, cap_seconds: float):
    """Diagnostic wall-clock cap around a provider call.

    The configured provider timeout does *not* bound total call duration (it is a
    per-operation httpx timeout). This wrapper enforces a hard wall clock so live
    sampling is bounded; on overflow it raises ``ProviderTimeout`` so the product's own
    fallback policy engages. It is a *diagnostic* control, not a product change.
    """
    import threading

    from app.llm.provider import ProviderTimeout

    class _Capped:
        def complete(self, prompt, *, model, timeout=30.0):
            box: dict = {}

            def _run():
                try:
                    box["value"] = inner.complete(prompt, model=model, timeout=timeout)
                except BaseException as exc:  # noqa: BLE001
                    box["error"] = exc

            thread = threading.Thread(target=_run, daemon=True)
            thread.start()
            thread.join(cap_seconds)
            if thread.is_alive():
                raise ProviderTimeout(
                    f"diagnostic wall-clock cap {cap_seconds:.0f}s exceeded")
            if "error" in box:
                raise box["error"]
            return box["value"]

    return _Capped()


def build_live(*, recorder: Recorder, runtime_dir: Path | None = None,
               use_store: bool = True, call_cap_seconds: float | None = None):
    """Live product runtime: real provider, real planner, real semantic layer."""
    from app.artifact_runtime.factory import build_runtime
    from app.artifact_runtime.planner import (LLMPlanner, LLMSemanticInterpreter)
    from app.artifact_runtime.response import LLMResponseComposer
    from app.llm.openai_provider import OpenAICompatibleProvider

    knowledge_store = SqliteKnowledgeStore(settings.knowledge_store_path)
    knowledge = KnowledgeBase(knowledge_store)
    store = None
    if use_store:
        store_dir = Path(runtime_dir) if runtime_dir else (AUDIT_ROOT / "_runtime_live")
        store_dir.mkdir(parents=True, exist_ok=True)
        store = SqliteOperationalStore(store_dir / "operational.db")

    inner_provider = OpenAICompatibleProvider(settings)
    if call_cap_seconds:
        inner_provider = _capping_provider(inner_provider, call_cap_seconds)
    timeout = max(settings.llm_request_timeout_seconds, 60.0)
    interpreter = ProfilingInterpreter(
        LLMSemanticInterpreter(ProfilingProvider(inner_provider, "semantic", recorder),
                               settings.llm_semantic_model, timeout=timeout), recorder)
    planner = ProfilingPlanner(
        LLMPlanner(ProfilingProvider(inner_provider, "planning", recorder),
                   settings.llm_planner_model, timeout=timeout), recorder)
    composer = ProfilingComposer(
        LLMResponseComposer(ProfilingProvider(inner_provider, "response", recorder),
                            settings.llm_response_model, timeout=timeout), recorder)
    web = _wrap_live_web(recorder)

    runtime = build_runtime(
        runtime_dir=str(store_dir) if use_store else str(AUDIT_ROOT / "_runtime_live_nostore"),
        use_llm=True, knowledge=knowledge, interpreter=interpreter, planner=planner,
        composer=composer, web=web, store=store, today=lambda: TODAY)
    instrument_runtime(runtime, recorder)
    return runtime, knowledge_store, store
