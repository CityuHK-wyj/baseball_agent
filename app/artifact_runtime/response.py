"""Response composition from accepted claims and evidence.

Normal output answers directly from accepted claims + Artifact references, and discloses
assumptions, coverage gaps and limitations. Rejected or ungrounded evidence is never
presented as if it were trustworthy, and internal ids/tool mechanics stay under trace.
"""

from __future__ import annotations

import json
from typing import Protocol

from app.llm.provider import ModelProvider, ProviderError
from app.models.artifact_runtime import Claim, Goal, RuntimeArtifact
from app.artifact_runtime.sufficiency import GoalCoverage

RESPONSE_PROMPT = """You are a baseball assistant. Answer the user in their own language
using ONLY the supported claims and quoted evidence below. Do not invent numbers. Answer
directly first, then the key supporting facts, then any stated limitations. Never mention
SQL, table names, internal ids, artifacts or requirements.

User question: {message}
Goal: {goal}
Supported claims:
{claims}
Limitations / coverage gaps: {gaps}

Write the final answer.
"""


class ResponseComposer(Protocol):
    def compose(self, *, message: str, goal: Goal, claims: tuple[Claim, ...],
                artifacts: tuple[RuntimeArtifact, ...], coverage: GoalCoverage,
                assumptions: tuple[str, ...]) -> str: ...


class DeterministicResponseComposer:
    """Renders accepted claims plus disclosed limitations. No unsupported synthesis."""

    def compose(self, *, message: str, goal: Goal, claims: tuple[Claim, ...],
                artifacts: tuple[RuntimeArtifact, ...], coverage: GoalCoverage,
                assumptions: tuple[str, ...]) -> str:
        if not claims:
            return ("I could not find evidence that actually supports an answer to that "
                    "yet. I did not want to guess.")
        lines = [claim.text for claim in claims if claim.text.strip()]
        text = "\n".join(lines)
        if assumptions:
            text += "\n\nAssumptions: " + "; ".join(assumptions)
        if not coverage.core_goal_supported:
            if coverage.gaps:
                text += "\n\nImportant limitations: " + "; ".join(coverage.gaps[:4])
            elif coverage.missing_needs:
                text += ("\n\nI could only partially cover the request; some parts remain "
                         "unsupported.")
        return text


class LLMResponseComposer:
    def __init__(self, provider: ModelProvider, model: str, timeout: float = 45.0,
                 fallback: ResponseComposer | None = None) -> None:
        self._provider = provider
        self._model = model
        self._timeout = timeout
        self._fallback = fallback or DeterministicResponseComposer()

    def compose(self, *, message: str, goal: Goal, claims: tuple[Claim, ...],
                artifacts: tuple[RuntimeArtifact, ...], coverage: GoalCoverage,
                assumptions: tuple[str, ...]) -> str:
        if not claims:
            return self._fallback.compose(message=message, goal=goal, claims=claims,
                                          artifacts=artifacts, coverage=coverage,
                                          assumptions=assumptions)
        prompt = RESPONSE_PROMPT.format(
            message=message, goal=goal.statement or message,
            claims=_render_claims(claims, artifacts),
            gaps="; ".join(coverage.gaps) or "(none)")
        try:
            response = self._provider.complete(prompt, model=self._model,
                                               timeout=self._timeout)
            text = (response.text or "").strip()
            if text:
                if not coverage.core_goal_supported and coverage.gaps:
                    text += "\n\nImportant limitations: " + "; ".join(coverage.gaps[:4])
                return text
        except ProviderError:
            pass
        return self._fallback.compose(message=message, goal=goal, claims=claims,
                                      artifacts=artifacts, coverage=coverage,
                                      assumptions=assumptions)


def _render_claims(claims: tuple[Claim, ...],
                   artifacts: tuple[RuntimeArtifact, ...]) -> str:
    by_ref: dict[str, RuntimeArtifact] = {}
    for artifact in artifacts:
        by_ref[artifact.artifact_id] = artifact
        for item in artifact.exports:
            by_ref[item.export_id] = artifact
    lines: list[str] = []
    for claim in claims:
        lines.append(f"- {claim.text}")
        seen: set[str] = set()
        for ref in claim.support_refs:
            artifact = by_ref.get(ref)
            if artifact is None or artifact.artifact_id in seen:
                continue
            seen.add(artifact.artifact_id)
            if artifact.text_content:
                lines.append("    evidence: " + artifact.text_content[:1500])
            elif artifact.structured_data:
                lines.append("    evidence: "
                             + json.dumps(artifact.structured_data, ensure_ascii=False)[:1500])
    return "\n".join(lines)
