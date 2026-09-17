"""OpenAI-compatible model provider.

The only module that imports a vendor SDK, and it does so lazily. Credentials come from
the environment; failures are redacted before they can surface.
"""

from app.config import Settings
from app.llm.provider import ModelResponse, ProviderError, ProviderTimeout
from app.tools.results import redact_secrets


class OpenAICompatibleProvider:
    def __init__(self, config: Settings) -> None:
        self._config = config

    def complete(self, prompt: str, *, model: str, timeout: float = 30.0) -> ModelResponse:
        from openai import OpenAI
        if not self._config.deepseek_api_key:
            raise ProviderError("No LLM credential is configured (set DEEPSEEK_API_KEY).")
        client = OpenAI(api_key=self._config.deepseek_api_key,
                        base_url=self._config.deepseek_base_url, timeout=timeout)
        try:
            response = client.chat.completions.create(
                model=model, messages=[{"role": "user", "content": prompt}])
        except Exception as error:  # noqa: BLE001 - normalize vendor errors
            summary = redact_secrets(f"{type(error).__name__}: {error}",
                                     (self._config.deepseek_api_key,))
            if "timeout" in type(error).__name__.lower():
                raise ProviderTimeout(summary) from None
            raise ProviderError(summary) from None
        if not response.choices:
            raise ProviderError("Provider returned no choices.")
        choice = response.choices[0]
        usage = getattr(response, "usage", None)
        return ModelResponse(
            text=getattr(choice.message, "content", "") or "", model=model,
            finish_reason=getattr(choice, "finish_reason", "stop") or "stop",
            usage={"prompt_tokens": getattr(usage, "prompt_tokens", 0),
                   "completion_tokens": getattr(usage, "completion_tokens", 0)} if usage else {})
