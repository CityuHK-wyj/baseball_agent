"""Claims and support references.

Important answer statements are represented as :class:`Claim` objects with explicit
support references so fluent unsupported synthesis is detectable. A claim is accepted
only when every support reference resolves to an accepted Artifact (or one of its
exports/references).
"""

from __future__ import annotations

import re

from app.models.artifact_runtime import (Claim, CoverageAssessment, Goal, Need,
                                         RuntimeArtifact)
from app.artifact_runtime.references import ReferenceStore

_NUMBER = re.compile(r"\d+(?:\.\d+)?")


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


def build_claims(goal: Goal, needs: tuple[Need, ...],
                 artifacts: tuple[RuntimeArtifact, ...],
                 assessments: tuple[CoverageAssessment, ...]) -> tuple[Claim, ...]:
    by_id = {item.artifact_id: item for item in artifacts}
    claims: list[Claim] = []
    for assessment in assessments:
        if assessment.verdict != "SATISFIED":
            continue
        for artifact_id in assessment.supported_claims:
            artifact = by_id.get(artifact_id)
            if artifact is None:
                continue
            text = _first_line(artifact.text_content) or (
                f"{artifact.kind} artifact {artifact.artifact_id}")
            claims.append(Claim(
                claim_id=f"claim-{artifact.artifact_id}",
                text=text, support_refs=_artifact_ref_ids(artifact),
                confidence=max(artifact.confidence, assessment.evidence_quality),
                scope=artifact.actual_scope))
    return tuple(claims)


def validate_claims(proposed: tuple[Claim, ...], artifacts: tuple[RuntimeArtifact, ...],
                    refs: ReferenceStore) -> tuple[Claim, ...]:
    """Keep only claims whose support references resolve to accepted artifacts."""
    known: set[str] = set()
    for artifact in artifacts:
        if artifact.status not in ("OK", "PARTIAL"):
            continue
        known.add(artifact.artifact_id)
        known.update(item.export_id for item in artifact.exports)
        known.update(artifact.references)
    accepted: list[Claim] = []
    for claim in proposed:
        support = tuple(ref for ref in claim.support_refs if ref in known)
        if not support:
            continue
        accepted.append(claim.model_copy(update={"support_refs": support}))
    return tuple(accepted)


def unsupported_numbers(claim: Claim, artifacts: tuple[RuntimeArtifact, ...]) -> tuple[str, ...]:
    """Numeric tokens in a claim that do not appear in any supporting artifact.

    Used as a lightweight anti-bluff check for composed prose; it never auto-rejects a
    claim but surfaces unsupported figures in the trace.
    """
    supported_text = " ".join(
        artifact.text_content + " " + str(artifact.structured_data)
        for artifact in artifacts
        if artifact.artifact_id in claim.support_refs
        or any(item.export_id in claim.support_refs for item in artifact.exports))
    haystack = set(_NUMBER.findall(supported_text))
    missing = [token for token in _NUMBER.findall(claim.text) if token not in haystack]
    return tuple(dict.fromkeys(missing))


def claim_support_summary(claims: tuple[Claim, ...]) -> tuple[str, ...]:
    return tuple(f"{claim.claim_id} -> {', '.join(claim.support_refs)}" for claim in claims)
