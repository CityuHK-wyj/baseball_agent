"""Bounded Judge inspection of already-retrieved Web source material (WSA drill-down).

The Judge may inspect evidence that was already acquired for the current Goal/Artifact. It
must never acquire more: there is no search, no fetch and no new URL here. The path is
strictly

    EvidenceFinding -> EvidenceSpan -> Representation -> RetrievedDocument

and the result is a provenance-preserving :class:`SourceInspection`. Raw source text is
untrusted data, delimited from instructions, and can never change a Requirement, a typed
expectation or a verifier hard gate.
"""

from __future__ import annotations

from typing import Any

from app.contracts.models import EvidenceFinding, SourceInspection, VerificationStatus

# A finding is inspected only when a *soft* semantic ambiguity -- not a hard gate -- left it
# short of support. Clear findings and hard conflicts are never re-opened.
_AMBIGUOUS_COMPATIBILITIES = {"INSUFFICIENT", ""}
_AMBIGUOUS_ASSESSMENTS = {"UNKNOWN", ""}
# WSA-04: an unresolved or non-entailing relation is exactly the soft ambiguity the Judge may
# inspect further. A decisive CONTRADICTS/ENTAILS relation is not re-opened by drill-down.
_AMBIGUOUS_RELATIONS = {"UNCERTAIN", "IRRELEVANT", ""}


def inspection_trigger(finding: EvidenceFinding) -> str | None:
    """Return a bounded trigger reason, or ``None`` when no inspection is warranted."""
    if finding.status == VerificationStatus.SUPPORTED:
        return None
    if finding.hard_conflict or finding.hard_reasons:
        return None
    compatibility = finding.semantic_compatibility or str(finding.checks.get("semantic_compatibility") or "")
    if compatibility not in _AMBIGUOUS_COMPATIBILITIES:
        return None
    assessment = finding.semantic_assessment or finding.checks.get("semantic_assessment") or {}
    assertion = str(assessment.get("assertion_strength") or finding.assertion_state or "")
    relevance = str(assessment.get("temporal_relevance") or "")
    relation = str(assessment.get("relation") or finding.relation_state or "")
    if (
        assertion in _AMBIGUOUS_ASSESSMENTS
        or relevance in _AMBIGUOUS_ASSESSMENTS
        or relation in _AMBIGUOUS_RELATIONS
    ):
        return "ambiguous_assessment"
    return "semantic_insufficiency"


def inspect_finding(
    finding: EvidenceFinding,
    artifacts: list[Any],
    *,
    level: int = 2,
    context_window: int = 600,
    max_representation_chars: int = 8000,
) -> SourceInspection | None:
    """Load bounded context for a finding from already-persisted material only."""
    location = _locate(finding, artifacts)
    if location is None:
        return None
    artifact, document, span = location
    representation = _representation(document, span.get("representation_id"))
    body = ""
    if representation is not None:
        body = str(representation.get("content") or "")
    if not body:
        body = str(document.get("body_text") or "")

    before = ""
    after = ""
    start = span.get("start")
    end = span.get("end")
    if isinstance(start, int) and isinstance(end, int) and body:
        before = body[max(0, start - context_window):start]
        after = body[end:end + context_window]

    representation_loaded = False
    if level >= 3 and body:
        # Level 3 is the already-retrieved representation itself, still bounded. It is never
        # pushed to a model by construction; the caller decides what to do with it.
        before = body[:max_representation_chars]
        after = ""
        representation_loaded = True

    return SourceInspection(
        finding_id=finding.id,
        proposition_id=finding.proposition_id,
        document_id=finding.document_id,
        representation_id=str(span.get("representation_id") or (representation or {}).get("id") or ""),
        span_id=finding.span_id,
        source_url=finding.source_url or str(document.get("url") or ""),
        content_hash=finding.content_hash or str(document.get("content_hash") or ""),
        publication_date=document.get("publication_date"),
        retrieved_at=finding.retrieved_at or str(document.get("retrieved_at") or ""),
        level=level,
        trigger=inspection_trigger(finding) or "manual",
        section=str(span.get("location") or ""),
        span_text=str(span.get("text") or ""),
        before=before,
        after=after,
        context_chars=len(before) + len(after),
        representation_loaded=representation_loaded,
        no_new_fetch=True,
        source="deterministic",
        resolved_status="",
        rationale="",
        assessment={},
    )


def _locate(finding: EvidenceFinding, artifacts: list[Any]):
    for artifact in artifacts:
        if getattr(artifact, "kind", "") != "WEB_EVIDENCE":
            continue
        data = getattr(artifact, "structured_data", {}) or {}
        spans = {str(span.get("id")): span for span in (data.get("evidence_spans") or []) if isinstance(span, dict)}
        if finding.span_id not in spans:
            continue
        documents = {str(doc.get("id")): doc for doc in (data.get("documents") or []) if isinstance(doc, dict)}
        document = documents.get(finding.document_id)
        if document is None:
            continue
        return artifact, document, spans[finding.span_id]
    return None


def _representation(document: dict, representation_id: Any) -> dict | None:
    for representation in document.get("representations") or []:
        if isinstance(representation, dict) and representation.get("id") == representation_id:
            return representation
    return None
