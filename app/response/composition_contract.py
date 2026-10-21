"""Composer-local typed output contract (WSA-06).

Inference and analytical synthesis belong *only* to the final Result Composer. This module
defines the response-local structure that lets the Composer distinguish what it is saying:

* ``FACTUAL``    - directly backed by an approved Claim (the only sourced assertions)
* ``QUALIFIED``  - backed by QualifiedEvidence, with its structural limitation retained
* ``ANALYSIS``   - the Composer's own synthesis / hypothesis / judgment / interpretation
* ``LIMITATION`` - information that remains unresolved or missing

None of these types is an upstream authority object. They are not Claims, Requirements,
EvidenceFindings, Artifacts or Shared Knowledge, and they are never persisted as such. They
exist only while the final answer is assembled and validated. ``supports``/``citations`` on an
``ANALYSIS`` statement are composer-local receipts naming which governed inputs informed the
inference; they are validation metadata, not a new evidence class.
"""

from __future__ import annotations

from enum import Enum
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, Field


class StatementKind(str, Enum):
    FACTUAL = "FACTUAL"
    QUALIFIED = "QUALIFIED"
    ANALYSIS = "ANALYSIS"
    LIMITATION = "LIMITATION"
    # The model returned plain prose (or a model without structured output is in use). The
    # deterministic conformance layer then applies conservative repair.
    UNTYPED = "UNTYPED"


class ComposedStatement(BaseModel):
    kind: StatementKind = StatementKind.UNTYPED
    text: str = ""
    # Premise receipts. For FACTUAL/QUALIFIED these are the citation markers the statement
    # relies on; for ANALYSIS they name the premises without being printed as direct support.
    citations: list[str] = Field(default_factory=list)
    supports: list[str] = Field(default_factory=list)


class ComposerDraft(BaseModel):
    """Untrusted Composer output; never a governed result or evidence authority."""

    answer_text: str = ""
    statements: list[ComposedStatement] = Field(default_factory=list)
    source: str = "model"  # model | fallback | deterministic


class StatementEpistemicRole(str, Enum):
    """The response-local epistemic role of one composed statement."""

    FACTUAL = "FACTUAL"
    QUALIFIED = "QUALIFIED"
    ANALYSIS = "ANALYSIS"
    LIMITATION = "LIMITATION"
    UNKNOWN = "UNKNOWN"


class StatementConformance(BaseModel):
    """One typed response-conformance assessment for a composed statement.

    This is an *assessment of representation*, never a truth judgment and never a new
    evidence authority. ``material_conclusion`` means the statement asserts causality,
    explanation, prediction, ranking/judgment, a latent relationship or an uncertain
    conclusion that no approved Claim directly states. ``framed_as_interpretation`` means it
    is presented as the model's own analysis rather than as source-established truth.
    """

    index: int = -1
    epistemic: StatementEpistemicRole = StatementEpistemicRole.UNKNOWN
    approved_claim_backed: bool = False
    material_conclusion: bool = False
    framed_as_interpretation: bool = False
    citations_on_inference: bool = False
    limitation_retained: bool = True
    # WSA-05 representation, not truth: does prose assert lineage or corroboration?
    origin_claim: bool = False
    origin_backed: bool = False
    # WSA-09: which approved fact markers the statement's factual content represents, and
    # whether it adds content no approved fact states. A citation marker alone is not proof.
    claim_markers: list[str] = Field(default_factory=list)
    unsupported_addition: bool = False
    qualified_markers: list[str] = Field(default_factory=list)
    limitation_ids: list[str] = Field(default_factory=list)
    unresolved_requirement_ids: list[str] = Field(default_factory=list)


class ResponseConformanceAssessment(BaseModel):
    """Bounded LLM response-conformance assessment. It classifies representation only."""

    statements: list[StatementConformance] = Field(default_factory=list)
    # Bound by the requesting runtime, never accepted from model-generated JSON.
    package_id: str = ""
    draft_id: str = ""

    def by_index(self, index: int) -> StatementConformance | None:
        for statement in self.statements:
            if statement.index == index:
                return statement
        return None


def parse_composer_draft(data: dict[str, Any] | None) -> ComposerDraft | None:
    """Normalize a model JSON object into the typed contract. Invalid input returns ``None``."""
    if not isinstance(data, dict):
        return None
    statements: list[ComposedStatement] = []
    raw_statements = data.get("statements")
    if isinstance(raw_statements, list):
        for entry in raw_statements:
            if not isinstance(entry, dict):
                continue
            text = str(entry.get("text") or "").strip()
            if not text:
                continue
            statements.append(
                ComposedStatement(
                    kind=_kind(entry.get("kind")),
                    text=text,
                    citations=[str(c).strip() for c in (entry.get("citations") or []) if str(c).strip()],
                    supports=[str(s).strip() for s in (entry.get("supports") or []) if str(s).strip()],
                )
            )
    answer_text = str(data.get("answer_text") or "").strip()
    if not statements and answer_text:
        statements = [ComposedStatement(kind=StatementKind.UNTYPED, text=answer_text)]
    if not statements:
        return None
    return ComposerDraft(
        answer_text=answer_text or " ".join(statement.text for statement in statements),
        statements=statements,
        source="model",
    )


def _kind(value: Any) -> StatementKind:
    try:
        return StatementKind(str(value or "").strip().upper())
    except ValueError:
        return StatementKind.UNTYPED


def draft_identity(draft: ComposerDraft) -> str:
    return sha256(draft.model_dump_json().encode()).hexdigest()


def parse_conformance_assessment(
    data: dict[str, Any] | None, *, package_id: str = "", draft_id: str = "",
) -> ResponseConformanceAssessment | None:
    """Validate representation proposals; omitted/ill-typed safety fields fail closed."""
    if not isinstance(data, dict):
        return None
    raw = data.get("statements")
    if not isinstance(raw, list):
        return None
    statements: list[StatementConformance] = []
    seen = set()
    bool_fields = {
        "approved_claim_backed", "material_conclusion", "framed_as_interpretation",
        "citations_on_inference", "limitation_retained", "origin_claim", "origin_backed",
        "unsupported_addition",
    }
    list_fields = {"claim_markers", "qualified_markers", "limitation_ids", "unresolved_requirement_ids"}
    for entry in raw:
        if not isinstance(entry, dict):
            return None
        if any(type(entry.get(name)) is not bool for name in bool_fields):
            return None
        if (type(entry.get("index")) is not int or entry["index"] < 0
                or entry["index"] in seen or _epistemic(entry.get("epistemic")) == StatementEpistemicRole.UNKNOWN):
            return None
        if "claim_markers" not in entry or any(
            not isinstance(entry.get(name, []), list)
            or any(not isinstance(item, str) for item in entry.get(name, [])) for name in list_fields
        ):
            return None
        index = entry["index"]
        seen.add(index)
        statements.append(
            StatementConformance(
                index=index,
                epistemic=_epistemic(entry.get("epistemic")),
                approved_claim_backed=_bool(entry.get("approved_claim_backed")),
                material_conclusion=_bool(entry.get("material_conclusion")),
                framed_as_interpretation=_bool(entry.get("framed_as_interpretation")),
                citations_on_inference=_bool(entry.get("citations_on_inference")),
                limitation_retained=_bool(entry.get("limitation_retained"), default=True),
                origin_claim=_bool(entry.get("origin_claim")),
                origin_backed=_bool(entry.get("origin_backed")),
                claim_markers=[str(m).strip().strip("[]") for m in (entry.get("claim_markers") or [])
                               if str(m).strip()],
                unsupported_addition=entry["unsupported_addition"],
                qualified_markers=entry.get("qualified_markers", []),
                limitation_ids=entry.get("limitation_ids", []),
                unresolved_requirement_ids=entry.get("unresolved_requirement_ids", []),
            )
        )
    if not statements:
        return None
    return ResponseConformanceAssessment(statements=statements, package_id=package_id, draft_id=draft_id)


def _epistemic(value: Any) -> StatementEpistemicRole:
    try:
        return StatementEpistemicRole(str(value or "").strip().upper())
    except ValueError:
        return StatementEpistemicRole.UNKNOWN


def _bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"true", "yes", "1"}
