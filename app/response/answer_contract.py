"""Answer-ready navigation contract between the Judge and the Result Composer (M2.6).

This is a *structured map*, not a lossy summary and not a second truth system. It records,
for each Requirement, what must be communicated, which Judge-accepted Claims carry it, where
the approved EvidenceSpans and source Documents are, and what remains unresolved. The
Composer reads this map (plus bounded source context) to write the answer.

Factual authority never moves here: ``approved_text`` is the exact accepted EvidenceSpan;
``context_text`` is surrounding source material for understanding only and MUST NOT be
asserted as fact.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from hashlib import sha256
import json
from typing import Any

from app.contracts.models import (
    CoverageState, Goal, AnswerProjection, QualifiedEvidence, RequirementAssessment, ArtifactAssessment,
    UserRequirement, AnswerProposition, Claim, SourceInspection, Clarification,
)
from app.contracts.immutability import freeze_value
from app.response.accepted_results import claim_factual_text, mandatory_claims
from app.contracts.evidence_origin import PropositionOrigins

_CONTEXT_PAD = 240
_MAX_CONTEXT_CHARS = 900


@dataclass(frozen=True)
class EvidenceContext:
    claim_id: str = ""
    finding_id: str = ""
    span_id: str = ""
    document_id: str = ""
    source: str = ""
    approved_text: str = ""
    context_text: str = ""
    retrieved_at: str = ""


@dataclass(frozen=True)
class AnswerFact:
    """One Judge-approved factual statement plus its citation marker and read-only context."""

    marker: str
    text: str
    claim_id: str
    requirement_id: str = ""
    proposition_statement: str = ""
    source: str = ""
    context: list[EvidenceContext] = field(default_factory=list)


@dataclass(frozen=True)
class RequirementAnswer:
    requirement_id: str
    statement: str
    kind: str
    required: bool
    coverage: str
    proposition_ids: list[str] = field(default_factory=list)
    claim_ids: list[str] = field(default_factory=list)
    evidence: list[EvidenceContext] = field(default_factory=list)

    @property
    def satisfied(self) -> bool:
        return self.coverage == CoverageState.SATISFIED.value


@dataclass(frozen=True)
class AnswerPackage:
    goal_id: str
    goal_status: str
    language: str
    facts: list[AnswerFact] = field(default_factory=list)
    requirements: list[RequirementAnswer] = field(default_factory=list)
    unresolved: list[RequirementAnswer] = field(default_factory=list)
    # WSA-03/presentation: useful but non-authoritative evidence. It is NOT a fact and does
    # not affect coverage; the Composer must state each item with its structural limitation.
    qualified_evidence: list[QualifiedEvidence] = field(default_factory=list)
    background_ok: bool = False
    limitations: list[str] = field(default_factory=list)
    evidence_origins: list[PropositionOrigins] = field(default_factory=list)
    governed_requirements: list[UserRequirement] = field(default_factory=list)
    requirement_assessments: list[RequirementAssessment] = field(default_factory=list)
    artifact_assessments: list[ArtifactAssessment] = field(default_factory=list)
    propositions: list[AnswerProposition] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    source_inspections: list[SourceInspection] = field(default_factory=list)
    citation_sources: dict[str, str] = field(default_factory=dict)
    clarification: Clarification | None = None
    package_id: str = field(init=False)

    def __post_init__(self):
        # Snapshot owns no mutable aliases to Goal, projection or raw artifacts.
        for item in fields(self):
            if item.name != "package_id":
                object.__setattr__(self, item.name, freeze_value(deepcopy(getattr(self, item.name))))
        payload = {item.name: getattr(self, item.name) for item in fields(self)
                   if item.name != "package_id"}
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=_json_value)
        object.__setattr__(self, "package_id", sha256(encoded.encode()).hexdigest())

    @property
    def satisfied_mandatory(self) -> list[RequirementAnswer]:
        return [r for r in self.requirements if r.required and r.satisfied]


def build_answer_package(
    goal: Goal,
    projection: AnswerProjection,
    artifacts: list[Any] | None = None,
    *,
    language: str = "en",
) -> AnswerPackage:
    findings, spans, documents = _index_artifacts(artifacts)
    requirements = {r.id: r for r in goal.requirements}
    propositions = list(projection.propositions)

    assessments = {assessment.requirement_id: assessment
                   for assessment in projection.requirement_assessments}
    # Navigation consumes the Judge's conclusion; legacy persisted projections alone
    # use the shared aggregation compatibility adapter.
    requirement_answers: list[RequirementAnswer] = []
    by_requirement: dict[str, RequirementAnswer] = {}
    for requirement in goal.requirements:
        related = [p for p in propositions if requirement.id in p.owner_requirement_ids]
        assessment = assessments.get(requirement.id)
        coverage = assessment.coverage.value if assessment else _requirement_coverage(related)
        entry = RequirementAnswer(
            requirement_id=requirement.id,
            statement=requirement.statement,
            kind=requirement.kind.value,
            required=requirement.required,
            coverage=coverage,
            proposition_ids=[p.id for p in related],
        )
        requirement_answers.append(entry)
        by_requirement[requirement.id] = entry

    claims_by_id = {claim.id: claim for claim in projection.claims}
    for proposition in propositions:
        for requirement_id in proposition.owner_requirement_ids:
            entry = by_requirement.get(requirement_id)
            if entry is None:
                continue
            for finding_id in proposition.evidence_finding_ids:
                entry.evidence.append(_evidence_from_finding(finding_id, findings, spans, documents))
            if proposition.supporting_claim_ids:
                entry.claim_ids.extend(proposition.supporting_claim_ids)

    # Approved facts are exactly the mandatory satisfied communicable claims, in citation order.
    facts: list[AnswerFact] = []
    for index, claim in mandatory_claims(projection):
        requirement_id = _claim_requirement(claim, propositions)
        statement = next(
            (p.statement for p in propositions if p.id in claim.proposition_ids),
            "",
        )
        contexts: list[EvidenceContext] = []
        if requirement_id and requirement_id in by_requirement:
            contexts = list(by_requirement[requirement_id].evidence)
        facts.append(
            AnswerFact(
                marker=f"S{index}",
                text=claim_factual_text(claim),
                claim_id=claim.id,
                requirement_id=requirement_id,
                proposition_statement=statement,
                source=_claim_source(claim),
                context=contexts,
            )
        )

    unresolved = [
        entry for entry in requirement_answers
        if entry.required and not entry.satisfied
    ]
    return AnswerPackage(
        goal_id=goal.id,
        goal_status=projection.goal_status.value,
        language=language,
        facts=facts,
        requirements=requirement_answers,
        unresolved=unresolved,
        qualified_evidence=list(getattr(projection, "qualified_evidence", []) or []),
        background_ok=projection.background_ok,
        limitations=list(projection.limitations),
        evidence_origins=list(projection.evidence_origins),
        governed_requirements=list(goal.requirements),
        requirement_assessments=list(projection.requirement_assessments),
        artifact_assessments=list(projection.artifact_assessments),
        propositions=list(projection.propositions),
        claims=list(projection.claims),
        source_inspections=list(projection.source_inspections),
        citation_sources={**{fact.marker: fact.source for fact in facts},
                          **{f"Q{i}": item.source_url for i, item in enumerate(projection.qualified_evidence, 1)}},
        clarification=projection.clarification,
    )


def render_governed_package(package: AnswerPackage) -> str:
    """Deterministic fallback; no Composer prose, labels or failed drafts are consulted."""
    from app.response.accepted_results import render_qualified_evidence
    from app.verification.evidence_origin import origin_presentation

    if package.clarification is not None:
        clarification = package.clarification
        return clarification.question + ("\n" + " / ".join(clarification.options) if clarification.options else "")
    chinese = package.language.startswith("zh")
    lines = [f"{' '.join(fact.text.split())} [{fact.marker}]" for fact in package.facts]
    qualified = render_qualified_evidence(package)
    if qualified:
        lines.append(("已找到但未能确认：" if chinese else "Found but not fully verified:") + "\n" + qualified)
    gaps = list(dict.fromkeys([*package.limitations, *(entry.statement for entry in package.unresolved)]))
    if gaps:
        lines.append(("未能确认：" if chinese else "Not established:") + "\n" + "\n".join(f"- {gap}" for gap in gaps))
    origins = origin_presentation(package.evidence_origins, language=package.language)
    if origins:
        lines.append(origins)
    return "\n\n".join(lines) or ("未能获得可支持的结论。" if chinese else "No supported conclusion could be produced.")


def _json_value(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"unsupported package value: {type(value).__name__}")


def _requirement_coverage(propositions: list[Any]) -> str:
    from app.verification.coverage import requirement_coverage

    return requirement_coverage(propositions).value


def _claim_requirement(claim: Any, propositions: list[Any]) -> str:
    for proposition in propositions:
        if proposition.id in claim.proposition_ids:
            return proposition.requirement_id or ""
    return ""


def _claim_source(claim: Any) -> str:
    for ref in claim.support_refs:
        if ref.source:
            return ref.source
    return ""


def _index_artifacts(artifacts: list[Any] | None):
    findings: dict[str, dict] = {}
    spans: dict[str, dict] = {}
    documents: dict[str, dict] = {}
    for artifact in artifacts or []:
        data = getattr(artifact, "structured_data", None) or {}
        for finding in data.get("evidence_findings", []) or []:
            if isinstance(finding, dict) and finding.get("id"):
                findings[str(finding["id"])] = finding
        for span in data.get("evidence_spans", []) or []:
            if isinstance(span, dict) and span.get("id"):
                spans[str(span["id"])] = span
        for document in data.get("documents", []) or []:
            if isinstance(document, dict) and document.get("id"):
                documents[str(document["id"])] = document
    return findings, spans, documents


def _evidence_from_finding(finding_id: str, findings, spans, documents) -> EvidenceContext:
    finding = findings.get(str(finding_id), {})
    span = spans.get(str(finding.get("span_id") or ""), {})
    document = documents.get(str(finding.get("document_id") or ""), {})
    approved = str(span.get("text") or "")
    context = _bounded_context(document.get("body_text") or "", span.get("start"), span.get("end"))
    return EvidenceContext(
        finding_id=str(finding_id),
        span_id=str(finding.get("span_id") or ""),
        document_id=str(finding.get("document_id") or ""),
        source=str(finding.get("source_url") or document.get("url") or ""),
        approved_text=approved,
        context_text=context,
        retrieved_at=str(finding.get("retrieved_at") or document.get("retrieved_at") or ""),
    )


def _bounded_context(body: str, start: Any, end: Any) -> str:
    if not body:
        return ""
    try:
        start_i = max(0, int(start) - _CONTEXT_PAD)
        end_i = min(len(body), int(end) + _CONTEXT_PAD)
    except (TypeError, ValueError):
        return body[:_MAX_CONTEXT_CHARS]
    return body[start_i:end_i][:_MAX_CONTEXT_CHARS]
