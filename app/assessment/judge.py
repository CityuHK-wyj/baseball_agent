"""Contextual Judge: interprets soft signals for one Requirement.

The Judge never overrides a hard deterministic failure. Default implementation is
deterministic and rule-based; an LLM Judge can replace it behind the same seam.
"""

from typing import Protocol

from app.models.artifacts import Artifact, DeterministicResult, JudgeResult
from app.models.contracts import ArtifactRequirement


class Judge(Protocol):
    def assess(self, artifact: Artifact, requirement: ArtifactRequirement,
               deterministic: DeterministicResult) -> JudgeResult: ...


_HARD_VETO = JudgeResult(level="REJECT", rationale="Hard deterministic failure cannot be overridden.")


class RuleBasedJudge:
    """Deterministic Judge. Uses the requirement's evidence purpose as context."""

    def assess(self, artifact: Artifact, requirement: ArtifactRequirement,
               deterministic: DeterministicResult) -> JudgeResult:
        if not deterministic.passed:
            return _HARD_VETO
        signals = deterministic.soft_signals
        if not signals:
            return JudgeResult(level="STRONG", rationale="No soft signals; deterministic facts align.")
        codes = tuple(sorted({signal.code for signal in signals}))
        if any(signal.code == "ZERO_ROWS" for signal in signals):
            return JudgeResult(level="REJECT", rationale="Artifact has no rows usable for this requirement.",
                               reinterpreted_signals=codes)
        major = sum(1 for signal in signals if signal.severity == "MAJOR")
        purpose = requirement.evidence_purpose
        if purpose == "EXISTENCE":
            return JudgeResult(
                level="ACCEPTABLE",
                rationale="A single observed instance is sufficient to establish existence.",
                reinterpreted_signals=codes)
        if purpose == "INFERENTIAL":
            if major >= 2:
                return JudgeResult(level="REJECT", rationale="Multiple major limitations undermine inference.",
                                   reinterpreted_signals=codes)
            if major == 1:
                return JudgeResult(level="WEAK", rationale="Limitations make inference only tentative.",
                                   reinterpreted_signals=codes)
            return JudgeResult(level="ACCEPTABLE", rationale="Minor limitations do not block inference.",
                               reinterpreted_signals=codes)
        # DESCRIPTIVE
        if major >= 2:
            return JudgeResult(level="REJECT", rationale="Multiple major limitations undermine a description.",
                               reinterpreted_signals=codes)
        if major == 1:
            return JudgeResult(level="WEAK", rationale="Coverage or sample limitations weaken the description.",
                               reinterpreted_signals=codes)
        return JudgeResult(level="ACCEPTABLE", rationale="Minor limitations are acceptable for description.",
                           reinterpreted_signals=codes)
