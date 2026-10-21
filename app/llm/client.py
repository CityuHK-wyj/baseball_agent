"""OpenAI-compatible LLM client with strict JSON extraction.

Roles are separate (semantic / planner / analytics / response) but share one thin client.
The client never logs prompts, provider payloads or secrets; it only returns parsed data
and usage.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

from app.config import LLMConfig


class LLMError(RuntimeError):
    """Raised when the provider fails or returns unusable output."""


@dataclass
class LLMResult:
    data: dict[str, Any]
    raw: str
    model: str
    latency_ms: int
    total_tokens: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> dict[str, Any]:
    """Best-effort extraction of a JSON object from a model response."""
    text = (text or "").strip()
    if not text:
        raise LLMError("empty model response")
    candidates: list[str] = []
    match = _JSON_BLOCK.search(text)
    if match:
        candidates.append(match.group(1).strip())
    candidates.append(text)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise LLMError("model response was not a JSON object")


class LLMClient:
    def __init__(self, config: LLMConfig):
        self.config = config
        self._client = None
        self.usage_sink = None
        self.usage_records: list[dict[str, Any]] = []
        # Explicit retry ownership: logical application attempts (this class) versus the
        # actual transport calls those attempts make. With provider retries disabled these
        # counters are 1:1, so telemetry can never report zero after real network attempts.
        self.application_attempts = 0
        self.provider_attempts = 0
        self.transport_attempts = 0  # actual HTTP sends, including SDK retries
        self.provider_retry_attempts = 0
        self._instrumented_http_clients = set()

    def _on_http_request(self, request):
        self.transport_attempts += 1
        retry = request.headers.get('x-stainless-retry-count', '0')
        if retry.isdecimal() and int(retry) > 0:
            self.provider_retry_attempts += 1

    def _instrument_http(self, sdk_client):
        # Also supports owned-provider fixtures injecting a real OpenAI client.
        http = getattr(sdk_client, '_client', None)
        hooks = getattr(http, 'event_hooks', None)
        if isinstance(hooks, dict) and id(http) not in self._instrumented_http_clients:
            hooks.setdefault('request', []).append(self._on_http_request)
            self._instrumented_http_clients.add(id(http))

    def _record_usage(self, response, role, format):
        usage = getattr(response, "usage", None)
        def field(name, alternate=None):
            value = getattr(usage, name, None) if usage is not None else None
            return value if value is not None else getattr(usage, alternate, None) if alternate and usage else None
        record = {"provider": self.config.base_url.split("//")[-1].split("/")[0].split("@")[-1],
                  "model": getattr(response, "model", None) or self._model_for(role),
                  "category": role, "format": format,
                  "input_tokens": field("prompt_tokens", "input_tokens"),
                  "output_tokens": field("completion_tokens", "output_tokens"),
                  "total_tokens": field("total_tokens")}
        record["usage_available"] = any(record[k] is not None for k in ("input_tokens", "output_tokens", "total_tokens"))
        record["application_attempt"] = self.application_attempts
        record['provider_attempts'] = self.provider_attempts
        record['provider_retry_attempts'] = self.provider_retry_attempts
        record['http_attempts'] = self.transport_attempts
        record["transport_attempts"] = self.transport_attempts
        record["provider_max_retries"] = self.config.provider_max_retries
        self.usage_records.append(record)
        if len(self.usage_records) > 500:
            del self.usage_records[:-500]
        if self.usage_sink:
            try:
                self.usage_sink(**record)
            except Exception:
                pass  # observability must never change provider-call behavior
        return record

    @property
    def configured(self) -> bool:
        return self.config.configured

    def _model_for(self, role: str) -> str:
        return {
            "semantic": self.config.semantic_model,
            "planner": self.config.planner_model,
            "analytics": self.config.analytics_model,
            "response": self.config.response_model,
        }.get(role, self.config.runtime_model)

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI  # imported lazily so offline tests can run
            except ImportError as exc:  # pragma: no cover
                raise LLMError(f"openai package unavailable: {exc}") from exc
            self._client = OpenAI(
                api_key=self.config.api_key,
                base_url=self.config.base_url,
                timeout=self.config.timeout_seconds,
                max_retries=self.config.provider_max_retries,
            )
        self._instrument_http(self._client)
        return self._client

    def complete_json(
        self,
        *,
        role: str,
        system: str,
        user: str,
        max_tokens: int = 1200,
        temperature: float = 0.0,
    ) -> LLMResult:
        if not self.config.configured:
            raise LLMError("LLM is not configured (missing API key)")
        model = self._model_for(role)
        client = self._get_client()
        last_error: Exception | None = None
        for attempt in range(2):
            self.application_attempts += 1
            started = time.monotonic()
            try:
                kwargs: dict[str, Any] = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "max_tokens": max_tokens + attempt * 600,
                    "temperature": temperature,
                    "response_format": {"type": "json_object"},
                }
                if self.config.reasoning_effort and self.config.reasoning_effort.lower() != "default":
                    kwargs["reasoning_effort"] = self.config.reasoning_effort
                self.provider_attempts += 1
                response = client.chat.completions.create(**kwargs)
            except Exception as exc:  # provider/network failure
                self._record_usage(None, role, "json")
                last_error = exc
                continue
            latency_ms = int((time.monotonic() - started) * 1000)
            measured = self._record_usage(response, role, "json")
            content = response.choices[0].message.content or ""
            try:
                data = extract_json(content)
            except LLMError as exc:
                last_error = exc
                continue
            usage = getattr(response, "usage", None)
            return LLMResult(
                data=data,
                raw=content,
                model=measured["model"],
                latency_ms=latency_ms,
                total_tokens=getattr(usage, "total_tokens", None) if usage else None,
                input_tokens=measured["input_tokens"],
                output_tokens=measured["output_tokens"],
            )
        raise LLMError(f"provider call failed: {type(last_error).__name__}: {last_error}")

    def complete_text(
        self,
        *,
        role: str,
        system: str,
        user: str,
        max_tokens: int = 1200,
        temperature: float = 0.2,
    ) -> tuple[str, int, str]:
        if not self.config.configured:
            raise LLMError("LLM is not configured (missing API key)")
        model = self._model_for(role)
        client = self._get_client()
        self.application_attempts += 1
        started = time.monotonic()
        try:
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            if self.config.reasoning_effort and self.config.reasoning_effort.lower() != "default":
                kwargs["reasoning_effort"] = self.config.reasoning_effort
            self.provider_attempts += 1
            response = client.chat.completions.create(**kwargs)
        except Exception as exc:
            self._record_usage(None, role, "text")
            raise LLMError(f"provider call failed: {type(exc).__name__}: {exc}") from exc
        latency_ms = int((time.monotonic() - started) * 1000)
        measured = self._record_usage(response, role, "text")
        return (response.choices[0].message.content or "").strip(), latency_ms, measured["model"]
