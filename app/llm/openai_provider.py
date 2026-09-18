"""OpenAI-compatible model provider.

The only module that imports a vendor SDK, and it does so lazily. Credentials come from
the environment; failures are redacted before they can surface.

Bounded interactive cognition:

* **streaming** completions so first-token latency is observable and a stalled stream can
  be aborted;
* a **true end-to-end deadline** (wall clock covering request, provider wait, stream reads
  and completion) — distinct from the per-operation SDK ``timeout`` which does not bound
  total duration;
* role-specific ``max_tokens`` and ``reasoning_effort`` so a structured role cannot emit
  an unbounded essay of hidden reasoning.

Nothing here changes what the model is allowed to *do*: it only bounds how long and how
large one inference call may be. Timeouts raise :class:`ProviderTimeout`; callers keep
their existing safe fallbacks.
"""

from __future__ import annotations

import time

from app.config import Settings
from app.llm.provider import ModelResponse, ProviderError, ProviderTimeout
from app.tools.results import redact_secrets


def _usage_dict(usage) -> dict[str, int]:
    if usage is None:
        return {}
    result = {
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
    }
    cached = getattr(usage, "prompt_cache_hit_tokens", None)
    if cached is None:
        details = getattr(usage, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", None) if details else None
    if cached is not None:
        result["cached_tokens"] = int(cached or 0)
    details = getattr(usage, "completion_tokens_details", None)
    reasoning = getattr(details, "reasoning_tokens", None) if details else None
    if reasoning is not None:
        result["reasoning_tokens"] = int(reasoning or 0)
    return result


class OpenAICompatibleProvider:
    def __init__(self, config: Settings) -> None:
        self._config = config

    def complete(self, prompt: str, *, model: str, timeout: float = 30.0,
                 max_tokens: int | None = None, reasoning_effort: str | None = None,
                 deadline: float | None = None, stream: bool | None = None) -> ModelResponse:
        from openai import OpenAI

        if not self._config.deepseek_api_key:
            raise ProviderError("No LLM credential is configured (set DEEPSEEK_API_KEY).")
        deadline_seconds = (deadline if deadline is not None
                            else getattr(self._config, "llm_deadline_seconds", None))
        use_stream = (getattr(self._config, "llm_stream", True)
                      if stream is None else stream)
        effort = (reasoning_effort if reasoning_effort is not None
                  else getattr(self._config, "llm_reasoning_effort", None))
        # Bound the SDK per-operation timeout by the end-to-end deadline as well, so a
        # stall *before* the first streamed token cannot outlive the deadline.
        client_timeout = timeout
        if deadline_seconds is not None:
            client_timeout = min(timeout, deadline_seconds)
        client = OpenAI(api_key=self._config.deepseek_api_key,
                        base_url=self._config.deepseek_base_url, timeout=client_timeout)
        start = time.perf_counter()
        try:
            if use_stream:
                return self._stream(client, prompt, model=model, max_tokens=max_tokens,
                                    reasoning_effort=effort, deadline=deadline_seconds,
                                    start=start)
            return self._blocking(client, prompt, model=model, max_tokens=max_tokens,
                                  reasoning_effort=effort, deadline=deadline_seconds,
                                  start=start)
        except ProviderError:
            raise
        except Exception as error:  # noqa: BLE001 - normalize vendor errors
            summary = redact_secrets(f"{type(error).__name__}: {error}",
                                     (self._config.deepseek_api_key,))
            if "timeout" in type(error).__name__.lower() or "timed out" in str(error).lower():
                raise ProviderTimeout(summary) from None
            raise ProviderError(summary) from None
        finally:
            try:
                client.close()
            except Exception:  # noqa: BLE001 - best-effort cleanup, never masks the result
                pass

    def _request_kwargs(self, *, max_tokens, reasoning_effort) -> dict:
        kwargs: dict = {}
        if max_tokens is not None:
            kwargs["max_tokens"] = int(max_tokens)
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort
        return kwargs

    def _stream(self, client, prompt: str, *, model: str, max_tokens, reasoning_effort,
                deadline, start: float) -> ModelResponse:
        kwargs = self._request_kwargs(max_tokens=max_tokens,
                                      reasoning_effort=reasoning_effort)
        stream_options = {"include_usage": True}
        try:
            stream = client.chat.completions.create(
                model=model, messages=[{"role": "user", "content": prompt}],
                stream=True, stream_options=stream_options, **kwargs)
        except Exception as error:  # noqa: BLE001 - usage options are optional
            if "stream_options" not in str(error).lower():
                raise
            stream = client.chat.completions.create(
                model=model, messages=[{"role": "user", "content": prompt}],
                stream=True, **kwargs)

        chunks: list[str] = []
        usage: dict[str, int] = {}
        finish_reason = "stop"
        first_token = 0.0
        try:
            for event in stream:
                elapsed = time.perf_counter() - start
                if deadline is not None and elapsed > deadline:
                    raise ProviderTimeout(
                        f"model call exceeded the end-to-end deadline of {deadline:.0f}s")
                event_usage = getattr(event, "usage", None)
                if event_usage is not None:
                    usage = _usage_dict(event_usage)
                choices = getattr(event, "choices", None) or []
                if not choices:
                    continue
                choice = choices[0]
                delta = getattr(choice, "delta", None)
                content = getattr(delta, "content", None) if delta is not None else None
                if content:
                    if not first_token:
                        first_token = elapsed
                    chunks.append(content)
                if getattr(choice, "finish_reason", None):
                    finish_reason = choice.finish_reason
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001
                    pass

        text = "".join(chunks)
        duration = time.perf_counter() - start
        if not text and finish_reason == "length":
            # Bounded output was fully consumed by hidden reasoning. This is a typed
            # bounded-output failure, not silent success.
            raise ProviderError(
                "model produced no visible content within max_tokens "
                f"(finish_reason=length, completion_tokens={usage.get('completion_tokens', 0)})")
        return ModelResponse(text=text, model=model, finish_reason=finish_reason,
                             usage=usage, first_token_seconds=round(first_token, 4),
                             duration_seconds=round(duration, 4),
                             reasoning_tokens=int(usage.get("reasoning_tokens", 0) or 0))

    def _blocking(self, client, prompt: str, *, model: str, max_tokens, reasoning_effort,
                  deadline, start: float) -> ModelResponse:
        import threading

        kwargs = self._request_kwargs(max_tokens=max_tokens,
                                      reasoning_effort=reasoning_effort)
        box: dict = {}

        def _run() -> None:
            try:
                box["response"] = client.chat.completions.create(
                    model=model, messages=[{"role": "user", "content": prompt}], **kwargs)
            except BaseException as error:  # noqa: BLE001 - re-raised by the caller
                box["error"] = error

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        thread.join(deadline if deadline is not None else None)
        if thread.is_alive():
            # Abort the in-flight request; the client is per-call and then discarded.
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
            thread.join(5.0)
            detail = (f"model call exceeded the end-to-end deadline of {deadline:.0f}s"
                      if deadline else "model call exceeded its deadline")
            raise ProviderTimeout(detail)
        if "error" in box:
            raise box["error"]
        response = box.get("response")
        if response is None or not getattr(response, "choices", None):
            raise ProviderError("Provider returned no choices.")
        choice = response.choices[0]
        duration = time.perf_counter() - start
        return ModelResponse(
            text=getattr(choice.message, "content", "") or "", model=model,
            finish_reason=getattr(choice, "finish_reason", "stop") or "stop",
            usage=_usage_dict(getattr(response, "usage", None)),
            duration_seconds=round(duration, 4))
