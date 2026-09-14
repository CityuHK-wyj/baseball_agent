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


class ProviderError(RuntimeError):
    """Raised for provider failures. The message is always pre-redacted."""


class ProviderTimeout(ProviderError):
    """A provider call exceeded its deadline; callers may retry."""


class ModelProvider(Protocol):
    def complete(self, prompt: str, *, model: str, timeout: float = 30.0) -> ModelResponse: ...


class FakeModelProvider:
    """Scripted provider for tests. Never touches the network."""

    def __init__(self, responses: list[str] | None = None, error: Exception | None = None) -> None:
        self._responses = list(responses or [])
        self._error = error
        self.calls: list[tuple[str, str]] = []

    def complete(self, prompt: str, *, model: str, timeout: float = 30.0) -> ModelResponse:
        self.calls.append((model, prompt))
        if self._error is not None:
            raise self._error
        text = self._responses.pop(0) if self._responses else ""
        return ModelResponse(text=text, model=model)
