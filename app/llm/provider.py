"""Provider-agnostic model access.

The domain layer depends on the ``ModelProvider`` Protocol, never on a vendor SDK. A
scripted ``FakeModelProvider`` is the tested default; ``OpenAICompatibleProvider`` is a
thin adapter for OpenAI-compatible endpoints.
"""

from typing import Protocol

from pydantic import Field

from app.models.artifacts import ArtifactContract


class ModelResponse(ArtifactContract):
    text: str = ""
    model: str = ""
    finish_reason: str = "stop"
    usage: dict[str, int] = Field(default_factory=dict)
    # Observable model-call telemetry (never prompt/chain-of-thought content).
    first_token_seconds: float = 0.0
    duration_seconds: float = 0.0
    reasoning_tokens: int = 0
    deadline_exceeded: bool = False


class ProviderError(RuntimeError):
    """Raised for provider failures. The message is always pre-redacted."""


class ProviderTimeout(ProviderError):
    """A provider call exceeded its deadline; callers may retry."""


class ModelProvider(Protocol):
    def complete(self, prompt: str, *, model: str, timeout: float = 30.0,
                 max_tokens: int | None = None, reasoning_effort: str | None = None,
                 deadline: float | None = None, stream: bool | None = None
                 ) -> ModelResponse: ...


class FakeModelProvider:
    """Scripted provider for tests. Never touches the network."""

    def __init__(self, responses: list[str] | None = None, error: Exception | None = None) -> None:
        self._responses = list(responses or [])
        self._error = error
        # Backwards-compatible (model, prompt) view used across the existing test suite.
        self.calls: list[tuple[str, str]] = []
        # Extended per-call record (bounds/deadline/reasoning) for new tests.
        self.records: list[dict] = []

    def complete(self, prompt: str, *, model: str, timeout: float = 30.0,
                 max_tokens: int | None = None, reasoning_effort: str | None = None,
                 deadline: float | None = None, stream: bool | None = None) -> ModelResponse:
        self.calls.append((model, prompt))
        self.records.append({"model": model, "prompt": prompt, "timeout": timeout,
                             "max_tokens": max_tokens, "reasoning_effort": reasoning_effort,
                             "deadline": deadline, "stream": stream})
        if self._error is not None:
            raise self._error
        text = self._responses.pop(0) if self._responses else ""
        return ModelResponse(text=text, model=model)
