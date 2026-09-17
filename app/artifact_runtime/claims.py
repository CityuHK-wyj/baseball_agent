"""Claims and support references.

Important answer statements are represented as :class:`Claim` objects with explicit
support references so fluent unsupported synthesis is detectable. A claim is accepted
only when:

* every support reference resolves to an accepted Artifact (or one of its exports), and
* the claim's *type* is supported by the evidence category. A statistical change alone
  must not support a causal explanation.

An Artifact pointer is not equivalent to "the proposition is entailed"; the claim type
records what kind of support is required.
"""

from __future__ import annotations

import re

from app.models.artifact_runtime import (Claim, CoverageAssessment, Goal, Need,
                                         RuntimeArtifact)
from app.artifact_runtime.references import ReferenceStore

_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_NARRATIVE_KINDS = ("web_evidence", "knowledge")
_MEASUREMENT_KINDS = ("analytics", "batting_stats", "derived", "ranked")

_INFERENCE_TOKENS = ("explain", "why", "cause", "reason", "导致", "原因", "为什么", "解释")


def _first_line(text: str, limit: int = 280) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return ""
    return "\n".join(lines[:3])[:limit]


def _artifact_ref_ids(artifact: RuntimeArtifact) -> tuple[str, ...]:
    refs = [item.export_id for item in artifact.exports]
    refs.extend(artifact.references)
    refs.append(artifact.artifact_id)
    return tuple(dict.fromkeys(refs))


def _claim_type(need: Need, artifact: RuntimeArtifact) -> str:
    text = f"{need.objective} {need.expected_information}".casefold()
    if "cause" in text or "导致" in text:
        return "CAUSAL_CLAIM"
    if any(token in text for token in _INFERENCE_TOKENS):
        return "REPORTED_EXPLANATION"
    if artifact.kind in _MEASUREMENT_KINDS:
        has_derived = any(item.export_type == "DERIVED_MEASURE" for item in artifact.exports)
        return "DERIVED_CALCULATION" if has_derived else "OBSERVED_FACT"
    return "OBSERVED_FACT"


def build_claims(goal: Goal, needs: tuple[Need, ...],
                 artifacts: tuple[RuntimeArtifact, ...],
                 assessments: tuple[CoverageAssessment, ...]) -> tuple[Claim, ...]:
    by_id = {item.artifact_id: item for item in artifacts}
    need_by_id = {item.need_id: item for item in needs}
    claims: list[Claim] = []
    for assessment in assessments:
        if assessment.verdict != "SATISFIED":
            continue
        need = need_by_id.get(assessment.need_id)
        for artifact_id in assessment.supported_claims:
            artifact = by_id.get(artifact_id)
            if artifact is None:
                continue
            text = _first_line(artifact.text_content) or (
                f"{artifact.kind} artifact {artifact.artifact_id}")
            claims.append(Claim(
                claim_id=f"claim-{artifact.artifact_id}",
                text=text, claim_type=_claim_type(need, artifact) if need else "OBSERVED_FACT",
                support_refs=_artifact_ref_ids(artifact),
                confidence=max(artifact.confidence, assessment.evidence_quality),
                scope=artifact.declared_scope))
    return tuple(claims)


def validate_claims(proposed: tuple[Claim, ...], artifacts: tuple[RuntimeArtifact, ...],
                    refs: ReferenceStore) -> tuple[Claim, ...]:
    """Keep only claims whose support resolves *and* whose type is evidenced.

    Causal/inferential claims that rest only on measurement are rejected rather than
    silently presented as supported facts.
    """
    by_ref: dict[str, RuntimeArtifact] = {}
    for artifact in artifacts:
        by_ref[artifact.artifact_id] = artifact
        for item in artifact.exports:
            by_ref[item.export_id] = artifact
        for reference in artifact.references:
            by_ref.setdefault(reference, artifact)
    accepted: list[Claim] = []
    for claim in proposed:
        support = tuple(ref for ref in claim.support_refs if ref in by_ref)
        if not support:
            continue
        supporting_artifacts = {by_ref[ref].artifact_id: by_ref[ref] for ref in support}
        kinds = {artifact.kind for artifact in supporting_artifacts.values()}
        if claim.claim_type == "CAUSAL_CLAIM":
            # Causation needs a sourced explanation *and* a measurement; otherwise the
            # claim is downgraded to an explicit hypothesis rather than asserted.
            if not (kinds & set(_NARRATIVE_KINDS)) or not (kinds & set(_MEASUREMENT_KINDS)):
                claim = claim.model_copy(update={"claim_type": "HYPOTHESIS"})
        elif claim.claim_type == "REPORTED_EXPLANATION":
            if not (kinds & set(_NARRATIVE_KINDS)):
                continue
        accepted.append(claim.model_copy(update={"support_refs": support}))
    return tuple(accepted)


def unsupported_numbers(claim: Claim, artifacts: tuple[RuntimeArtifact, ...]) -> tuple[str, ...]:
    """Numeric tokens in a claim that do not appear in any supporting artifact."""
    supported_text = " ".join(
        artifact.text_content + " " + str(artifact.structured_data)
        for artifact in artifacts
        if artifact.artifact_id in claim.support_refs
        or any(item.export_id in claim.support_refs for item in artifact.exports))
    haystack = set(_NUMBER.findall(supported_text))
    missing = [token for token in _NUMBER.findall(claim.text) if token not in haystack]
    return tuple(dict.fromkeys(missing))


def claim_support_summary(claims: tuple[Claim, ...]) -> tuple[str, ...]:
    return tuple(f"{claim.claim_id} [{claim.claim_type}] -> {', '.join(claim.support_refs)}"
                 for claim in claims)
