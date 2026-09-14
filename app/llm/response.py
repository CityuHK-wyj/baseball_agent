"""LLM Response composer behind a small Protocol.

The composer only expresses accepted products; it never re-derives facts, recomputes
metrics, re-assesses artifacts or replans. A deterministic composer is the fallback.
"""

import json
from typing import Protocol

from app.llm.prompts import RESPONSE_PROMPT, PromptTemplate
from app.llm.provider import ModelProvider, ProviderError
from app.models.reports import ResponsePackage


class ResponseComposer(Protocol):
    def compose(self, package: ResponsePackage) -> str: ...


class DeterministicResponseComposer:
    def compose(self, package: ResponsePackage) -> str:
        lines = [f"Objective {package.objective_ref}: {package.objective_status}"]
        for item in package.accepted_evidence:
            lines.append(f"- [{item.level}] {item.artifact_ref} from {item.source}: {item.summary}")
        if package.limitations:
            lines.append("Limitations: " + "; ".join(package.limitations))
        if package.unresolved_items:
            lines.append("Unresolved: " + ", ".join(package.unresolved_items))
        return "\n".join(lines)


class LLMResponseComposer:
    def __init__(self, provider: ModelProvider, model: str,
                 prompt: PromptTemplate = RESPONSE_PROMPT,
                 fallback: ResponseComposer | None = None, timeout: float = 30.0) -> None:
        self._provider = provider
        self._model = model
        self._prompt = prompt
        self._fallback = fallback or DeterministicResponseComposer()
        self._timeout = timeout

    def compose(self, package: ResponsePackage) -> str:
        evidence = [{"artifact": item.artifact_ref, "level": item.level, "summary": item.summary,
                     "source": item.source} for item in package.accepted_evidence]
        prompt = self._prompt.render(
            objective_ref=package.objective_ref, objective_status=package.objective_status,
            evidence_json=json.dumps(evidence, ensure_ascii=False),
            limitations="; ".join(package.limitations) or "none")
        try:
            return self._provider.complete(prompt, model=self._model, timeout=self._timeout).text
        except ProviderError:
            return self._fallback.compose(package)
