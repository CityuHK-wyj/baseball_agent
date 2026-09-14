"""LLM Judge behind the existing Judge Protocol.

The deterministic layer remains the tested default. The LLM judge may reinterpret soft
signals, but it never overrides a hard deterministic failure: that path returns REJECT
without calling the provider.
"""

import json

from app.assessment.judge import Judge
from app.llm.parsing import parse_json_object
from app.llm.prompts import JUDGE_PROMPT, PromptTemplate
from app.llm.provider import ModelProvider, ProviderError
from app.models.artifacts import Artifact, DeterministicResult, JudgeResult
from app.models.contracts import ArtifactRequirement

_LEVELS = ("STRONG", "ACCEPTABLE", "WEAK", "REJECT")


class LLMJudge:
    def __init__(self, provider: ModelProvider, model: str,
                 prompt: PromptTemplate = JUDGE_PROMPT,
                 fallback: Judge | None = None, timeout: float = 30.0) -> None:
        self._provider = provider
        self._model = model
        self._prompt = prompt
        self._fallback = fallback
        self._timeout = timeout

    def assess(self, artifact: Artifact, requirement: ArtifactRequirement,
               deterministic: DeterministicResult) -> JudgeResult:
        if not deterministic.passed:
            return JudgeResult(level="REJECT",
                               rationale="Hard deterministic failure cannot be overridden.")
        try:
            prompt = self._prompt.render(
                artifact_json=artifact.model_dump_json(),
                requirement_json=requirement.model_dump_json(),
                deterministic_json=deterministic.model_dump_json())
            response = self._provider.complete(prompt, model=self._model, timeout=self._timeout)
            return self._parse(response.text)
        except (ProviderError, ValueError, KeyError):
            if self._fallback is not None:
                return self._fallback.assess(artifact, requirement, deterministic)
            raise

    @staticmethod
    def _parse(text: str) -> JudgeResult:
        data = parse_json_object(text)
        level = str(data.get("level", "")).upper()
        if level not in _LEVELS:
            raise ValueError(f"Invalid judge level {data.get('level')!r}")
        return JudgeResult(level=level, rationale=data.get("rationale", "llm judgement"))
