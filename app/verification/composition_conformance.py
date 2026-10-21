"""Post-composition epistemic conformance (WSA-06).

The Result Composer is the only layer allowed to synthesise, interpret and infer. This module
does not decide whether an inference is true. It checks that the *epistemic status* of every
statement in the composed answer is represented correctly and enforces the response policy:

* a ``FACTUAL`` statement is backed by an approved Claim and invents no number;
* ``QUALIFIED`` evidence keeps its structural limitation and its provenance;
* ``ANALYSIS`` is not dressed up as a sourced fact (no citation markers on an inference) and a
  material conclusion is framed as interpretation;
* unresolved Requirements and limitations are not silently dropped;
* citations are never attached to model inference in a way that implies direct support.

Open-world interpretation of whether a statement is factual, causal, tentative or analytical
is an **LLM semantic-assessment task** (``request_conformance_assessment``). Deterministic code
only validates the resulting typed state and enforces repair. The finite cue tuple below is a
**safety tripwire**: a match may only require interpretation framing or force the bounded
rendering. A cue match never establishes support, and the *absence* of a cue never proves that
a statement is non-causal, tentative or safely framed. Likewise, words such as ``may`` or
``我的分析`` do not by themselves certify an inference: the typed assessment (or fail-closed
policy) does.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.response.accepted_results import render_qualified_evidence
from app.response.answer_contract import AnswerPackage
from app.response.composition_contract import (
    ComposedStatement,
    ComposerDraft,
    ResponseConformanceAssessment,
    StatementConformance,
    StatementEpistemicRole,
    parse_conformance_assessment,
    draft_identity,
)
from app.verification.evidence_origin import origin_presentation

# ------------------------------------------------------------------ safety tripwire only

# A match below can only force interpretation framing / fail closed. It never proves a
# statement is non-causal, and its absence never proves safety.
_CAUSAL_TRIPWIRES = (
    "caused", "causes", "causing", "led to", "lead to", "leads to", "resulted in", "results in",
    "because of", "due to", "as a result of", "responsible for", "brought about",
    "attributable to", "stemmed from", "导致", "造成", "引发", "使得", "归因于", "由于",
)
_CONFIRMATION_TRIPWIRES = (
    "confirmed that", "confirms that", "proves that", "proven that", "demonstrates that",
    "establishes that", "definitively", "without doubt", "conclusively",
    "证实", "证明", "确认", "无疑",
)

_ANALYSIS_LEAD = {"zh": "我的分析：", "en": "My analysis: "}
_QUALIFIED_HEADER = {"zh": "已找到但未能确认：", "en": "Found but not fully verified:"}
_NOT_ESTABLISHED_HEADER = {"zh": "未能确认：", "en": "Not established:"}
_MARKER_RE = re.compile(r"\[([SQ]\d+)\]")
_NUMBER_RE = re.compile(r"\d*\.\d+|\d+")

CONFORMANCE_SYSTEM = """You are the response-conformance assessor of a Baseball Agent.

You do NOT decide whether an inference is true, and you do NOT re-search, fetch, inspect new
evidence or approve new facts. You only classify how the composed answer represents the
governed inputs so deterministic code can apply the response policy.

You receive GOVERNED INPUTS (authoritative) and COMPOSED STATEMENTS (untrusted answer text).
Classify EACH composed statement and return JSON:
{"statements":[{"index":0,
  "epistemic":"FACTUAL|QUALIFIED|ANALYSIS|LIMITATION",
  "approved_claim_backed":true,
  "material_conclusion":false,
  "framed_as_interpretation":false,
  "citations_on_inference":false,
  "limitation_retained":true,"origin_claim":false,"origin_backed":false,
  "claim_markers":["S1"],"unsupported_addition":false,
  "qualified_markers":[],"limitation_ids":[],"unresolved_requirement_ids":[]}]}

Definitions:
- epistemic: the statement's actual role. FACTUAL is a sourced assertion directly stated by
  an APPROVED FACT. QUALIFIED is useful evidence that is not approved. ANALYSIS is the
  composer's own synthesis/hypothesis/judgment. LIMITATION states what is unresolved.
- approved_claim_backed: the factual content is directly stated by an APPROVED FACT (paraphrase
  is fine); false for any inference, explanation or unsourced addition.
- material_conclusion: the statement asserts causality, an explanation, a prediction, a
  ranking/judgment, a latent relationship or an uncertain conclusion that no approved Claim
  directly states.
- framed_as_interpretation: the statement is presented as the composer's own analysis rather
  than as source-established truth.
- citations_on_inference: a citation marker is attached to a conclusion that is not directly
  approved, so it would falsely imply direct support.
- limitation_retained: for qualified content, its structural limitation/provenance is still
  visible.

- origin_claim: the statement asserts source dependency, separate sourcing, independent
  confirmation, or uses source count to strengthen a fact. This applies to ALL statement kinds.
- origin_backed: the ENTIRE origin statement is justified by governed evidence_origins;
  any event facts it also asserts must be directly backed by approved facts (qualified facts
  must retain their limitations). An origin explanation cannot smuggle new factual content.
  Missing dependency is UNKNOWN, never separate. Different authors/domains/wording prove nothing.
  Counts cannot confirm speculation, restore stale evidence, override authority or resolve conflict.
  Claims of separate sourcing with no origin contract, or stronger assertion than the governed
  evidence, are NOT origin_backed. Do not confuse origin metadata with approval of event facts.
  Typed relation/group states and dependency_unresolved govern origin interpretation; free-text
  basis descriptions are explanatory proposals, never proof that overrides those states.
- claim_markers: for a FACTUAL statement, the APPROVED FACT markers whose content the statement
  actually represents. A valid citation marker is NOT proof that the whole sentence is approved.
  List only markers whose approved text the statement expresses.
- unsupported_addition: true for ANY statement that adds unauthorized factual content,
  including a second factual clause, invented comparison, qualified-to-fact promotion or an
  invented limitation. Composer labels are hints only, including ANALYSIS and LIMITATION.
  Genuine clearly represented analytical synthesis is allowed as ANALYSIS, not sourced truth.
- qualified_markers: Q markers actually represented, with the entire qualified meaning retained.
- limitation_ids: L identifiers of governed limitations faithfully represented.
- unresolved_requirement_ids: IDs of real unresolved mandatory requirements represented.
Every boolean field is required and must be a JSON boolean. Do not omit statements or fields.
Classify representation only. Do not judge whether the inference is correct or reasonable.
"""


@dataclass
class ConformanceViolation:
    kind: str
    detail: str


@dataclass
class ConformanceResult:
    """Representation verdict over one (AnswerPackage, ComposerDraft) pair."""

    text: str
    violations: list[ConformanceViolation] = field(default_factory=list)

    @property
    def repaired(self) -> bool:
        return bool(self.violations)


# ------------------------------------------------------------------ assessment request


def request_conformance_assessment(
    llm,
    package: AnswerPackage,
    draft: ComposerDraft,
    *,
    language: str = "en",
) -> ResponseConformanceAssessment | None:
    """Ask the LLM for a bounded representation assessment. ``None`` on any failure.

    The assessor cannot search, fetch, create a Requirement or approve a Claim. It only
    classifies the already-composed statements against the governed package.
    """
    if llm is None or not getattr(llm, "configured", False):
        return None
    if not draft.statements:
        return None
    packet = build_conformance_packet(package, draft, language=language)
    try:
        response = llm.complete_json(
            role="response",
            system=CONFORMANCE_SYSTEM,
            user=json.dumps(packet, ensure_ascii=False),
            max_tokens=min(2400, 200 + 200 * len(draft.statements)),
        )
    except Exception:  # noqa: BLE001 - a failed assessment fails closed, never fabricates safety
        return None
    return parse_conformance_assessment(
        getattr(response, "data", {}) or {}, package_id=package.package_id,
        draft_id=draft_identity(draft),
    )


def build_conformance_packet(package: AnswerPackage, draft: ComposerDraft, *, language: str = "en") -> dict:
    facts = [{"marker": fact.marker, "text": fact.text} for fact in package.facts]
    qualified = [
        {"marker": f"Q{index}", "text": item.span_text, "limitation": item.limitation}
        for index, item in enumerate(package.qualified_evidence, start=1)
    ]
    statements = [
        {"index": index, "composer_label": statement.kind.value, "text": statement.text}
        for index, statement in enumerate(draft.statements)
    ]
    return {
        "language": language,
        "goal_status": package.goal_status,
        "approved_facts": facts,
        "qualified_evidence": qualified,
        "package_id": package.package_id,
        "unresolved": [{"requirement_id": entry.requirement_id, "statement": entry.statement}
                       for entry in package.unresolved],
        "limitations": [{"id": f"L{index}", "text": text}
                        for index, text in enumerate(package.limitations, 1)],
        "composed_statements": statements,
        "evidence_origins": [o.model_dump(mode="json", exclude={"llm_proposal", "deterministic_signals", "validation_notes"})
                             for o in package.evidence_origins],
        "instruction": "Classify representation for each composed statement by index.",
    }


# ------------------------------------------------------------------ deterministic policy


def conform_composition(
    draft: ComposerDraft,
    package: AnswerPackage,
    assessment: ResponseConformanceAssessment | None = None,
    *,
    language: str = "en",
) -> ConformanceResult:
    """Validate and deterministically repair a composed answer against the governed package."""
    ctx = _Context(package=package, language=language)
    violations: list[ConformanceViolation] = []
    rendered: list[str] = []

    if assessment is not None and not isinstance(assessment, ResponseConformanceAssessment):
        violations.append(ConformanceViolation("MALFORMED_ASSESSMENT", "invalid semantic review schema"))
        assessment = None
    if assessment is not None and (
        assessment.package_id != package.package_id or assessment.draft_id != draft_identity(draft)
        or len({v.index for v in assessment.statements}) != len(assessment.statements)
        or any(v.index < 0 or v.index >= len(draft.statements) for v in assessment.statements)
    ):
        violations.append(ConformanceViolation("ASSESSMENT_IDENTITY_MISMATCH", "assessment is not bound to this package/draft"))
        assessment = None
    if not draft.statements:
        violations.append(ConformanceViolation("UNASSESSED_DRAFT", "free answer text is not governed authority"))

    for index, statement in enumerate(draft.statements):
        verdict = assessment.by_index(index) if assessment is not None else None
        text, statement_violations = _apply_statement(statement, verdict, ctx)
        violations.extend(statement_violations)
        if text and text.strip():
            rendered.append(text.strip())

    facts_block, facts_violations = _ensure_mandatory_facts(rendered, ctx)
    violations.extend(facts_violations)
    if facts_block:
        rendered.append(facts_block)

    qualified_block, qualified_violations = _ensure_qualified_lane(rendered, ctx)
    violations.extend(qualified_violations)
    if qualified_block:
        rendered.append(qualified_block)

    limitation_block, limitation_violations = _ensure_limitations(rendered, ctx)
    violations.extend(limitation_violations)
    if limitation_block:
        rendered.append(limitation_block)

    origins = origin_presentation(package.evidence_origins, language=language)
    if origins:
        rendered.append(origins)
    return ConformanceResult(text=_join(rendered, ctx.language), violations=violations)


def _apply_statement(statement: ComposedStatement, verdict: StatementConformance | None, ctx: "_Context"):
    if verdict is None or verdict.epistemic == StatementEpistemicRole.UNKNOWN:
        return "", [ConformanceViolation("MISSING_SEMANTIC_CONFORMANCE", "unassessed prose omitted; governed rendering required")]
    if verdict.unsupported_addition:
        return "", [ConformanceViolation("UNSUPPORTED_FACTUAL", "draft adds factual meaning not authorized by the package")]
    if verdict.origin_claim:
        if not verdict.origin_backed or not ctx.package.evidence_origins:
            # Do not reframe invented dependency as model analysis: it is a governed
            # provenance assertion, not an analytical inference the Composer may create.
            return "", [ConformanceViolation("ORIGIN_OVERCLAIM", "origin assertion lacks governed support")]
        # Preserve the WSA-06 numeric gate even on governed origin explanations.
        unsupported = _unsupported_numbers(statement.text, ctx.all_evidence)
        if unsupported:
            return "", [ConformanceViolation("INVENTED_NUMERIC", "origin prose contains unapproved numeric facts")]
        # WSA-06 remains active: origin prose is not an exemption from causal/framing
        # policy or the qualified-evidence limitation policy.
        valid = [m for m in _markers(statement.text) + _declared_markers(statement) if m in ctx.fact_markers]
        if _material_not_in_premises(statement.text, valid, ctx):
            return "", [ConformanceViolation("UNSUPPORTED_CAUSAL", "origin metadata cannot establish causality")]
        epistemic = _effective_epistemic(statement, verdict)
        if epistemic == StatementEpistemicRole.QUALIFIED:
            return _apply_qualified(statement, verdict, ctx)
        if epistemic == StatementEpistemicRole.ANALYSIS or verdict.material_conclusion:
            return _apply_analysis(statement, verdict, ctx)
        if not verdict.limitation_retained:
            return "", [ConformanceViolation("MISSING_LIMITATION", "origin explanation lost governed limitations")]
        # Source markers support event premises, not a set-level dependency assessment.
        return _strip_markers(statement.text, None), []
    epistemic = _effective_epistemic(statement, verdict)
    if epistemic == StatementEpistemicRole.FACTUAL:
        return _apply_factual(statement, verdict, ctx)
    if epistemic == StatementEpistemicRole.QUALIFIED:
        return _apply_qualified(statement, verdict, ctx)
    if epistemic == StatementEpistemicRole.LIMITATION:
        valid_limits = {f"L{i}" for i in range(1, len(ctx.package.limitations) + 1)}
        valid_requirements = {entry.requirement_id for entry in ctx.package.unresolved}
        if (not verdict.limitation_retained
                or set(verdict.limitation_ids) - valid_limits
                or set(verdict.unresolved_requirement_ids) - valid_requirements
                or not (verdict.limitation_ids or verdict.unresolved_requirement_ids)):
            return "", [ConformanceViolation("INVALID_LIMITATION", "limitation is not linked to governed unresolved meaning")]
        ctx.represented_limitations.update(verdict.limitation_ids)
        ctx.represented_requirements.update(verdict.unresolved_requirement_ids)
        return _strip_markers(statement.text, None), []
    return _apply_analysis(statement, verdict, ctx)


def _effective_epistemic(statement: ComposedStatement, verdict: StatementConformance | None) -> StatementEpistemicRole:
    # Composer self-labels never supply classification or authority, even during outage.
    return verdict.epistemic if verdict is not None else StatementEpistemicRole.UNKNOWN


def _apply_factual(statement: ComposedStatement, verdict: StatementConformance | None, ctx: "_Context"):
    text = statement.text.strip()
    violations: list[ConformanceViolation] = []
    markers = _markers(text) + _declared_markers(statement)
    valid = [marker for marker in markers if marker in ctx.fact_markers]
    unknown = [marker for marker in markers if marker not in ctx.fact_markers]
    if unknown:
        violations.append(ConformanceViolation(
            "UNKNOWN_CITATION", f"factual statement cites unknown marker(s): {sorted(set(unknown))}"))
        text = _strip_markers(text, set(unknown))

    # Safety tripwire: a causal/confirmation cue in a *factual* statement is only admissible
    # when a cited approved premise itself states that relation. The cue cannot grant truth and
    # its absence proves nothing; a match can only remove factual authority (fail closed).
    if _material_not_in_premises(text, valid, ctx):
        violations.append(ConformanceViolation(
            "UNSUPPORTED_CAUSAL",
            "causal/confirmation language in a factual statement is not stated by any cited approved claim"))
        return _canonical_for(valid, ctx), violations

    # WSA-09: a citation marker is not entailment, and a citation is not proof that the whole
    # sentence is approved. The representation assessor names the approved claim(s) the
    # statement actually represents; deterministic code validates identity and fails closed.
    if verdict is None:
        if valid:
            violations.append(ConformanceViolation(
                "UNASSESSED_FACTUAL", "no representation assessment; factual text cannot exceed approved claims"))
            return _canonical_for(valid, ctx), violations
        violations.append(ConformanceViolation(
            "UNSOURCED_FACTUAL", "factual statement is not backed by an approved claim"))
        return "", violations
    named = [marker for marker in verdict.claim_markers if marker in ctx.fact_markers]
    foreign = [marker for marker in verdict.claim_markers if marker not in ctx.fact_markers]
    if foreign:
        violations.append(ConformanceViolation(
            "UNKNOWN_CITATION", f"assessor named unknown claim marker(s): {sorted(set(foreign))}"))
    if verdict.unsupported_addition:
        violations.append(ConformanceViolation(
            "UNSUPPORTED_FACTUAL", "a factual statement adds content no named approved claim states"))
        return _canonical_for(named or valid, ctx), violations
    if not verdict.approved_claim_backed:
        violations.append(ConformanceViolation(
            "UNSUPPORTED_FACTUAL", "a factual statement is not directly stated by an approved claim"))
        return _downgrade_or_canonical(text, verdict, named or valid, ctx), violations
    if not named:
        # Affirmed as backed but no approved claim named: the citation token alone is not
        # factual authority, so render the cited approved text canonically.
        violations.append(ConformanceViolation(
            "UNASSESSED_FACTUAL", "no approved claim was named for the factual content"))
        return _canonical_for(valid, ctx), violations
    if set(valid) - set(named):
        violations.append(ConformanceViolation(
            "INVALID_CITATION_REPRESENTATION", "citation does not identify the represented approved Claim"))
        return _canonical_for(named, ctx), violations
    if named:
        valid = named

    evidence = ctx.evidence_for(valid) or ctx.approved_evidence
    unsupported = _unsupported_numbers(text, evidence)
    if unsupported:
        violations.append(ConformanceViolation(
            "INVENTED_NUMERIC", f"factual statement contains unapproved number(s): {sorted(set(unsupported))}"))
        return _canonical_for(valid, ctx), violations

    if valid and not _markers(text):
        # Keep the citation next to the statement it supports (WSA-06 §14).
        text = f"{text} " + " ".join(f"[{marker}]" for marker in dict.fromkeys(valid))
    return text, violations


def _apply_qualified(statement: ComposedStatement, verdict: StatementConformance | None, ctx: "_Context"):
    text = statement.text.strip()
    violations: list[ConformanceViolation] = []
    named = list(verdict.qualified_markers) if verdict is not None else []
    item = ctx.qualified_markers.get(named[0]) if len(named) == 1 else None
    if item is None:
        violations.append(ConformanceViolation(
            "UNSOURCED_QUALIFIED", "qualified statement is not backed by a governed qualified input"))
        return "", violations

    markers = _markers(text) + _declared_markers(statement)
    unknown = [marker for marker in markers if marker not in ctx.qualified_markers]
    if unknown:
        text = _strip_markers(text, set(unknown))
    if (set(markers) & set(ctx.qualified_markers)) - set(named):
        return "", [ConformanceViolation("INVALID_CITATION_REPRESENTATION", "qualified citation identity mismatch")]

    confirmation_tripwire = _tripwire(text, _CONFIRMATION_TRIPWIRES)
    assessor_lost_limitation = verdict is not None and not verdict.limitation_retained
    if assessor_lost_limitation or confirmation_tripwire:
        violations.append(ConformanceViolation(
            "MISSING_LIMITATION", "qualified evidence must keep its structural limitation"))
        # Replacing, rather than appending to a promoted factual assertion, preserves
        # the acquired proposition and its qualification without laundering the draft.
        text = f"{item.span_text} — not established: {item.limitation}"

    evidence = ctx.qualified_evidence_for(item) or ctx.approved_evidence
    unsupported = _unsupported_numbers(text, evidence)
    if unsupported:
        violations.append(ConformanceViolation(
            "INVENTED_NUMERIC", f"qualified statement contains unsupported number(s): {sorted(set(unsupported))}"))
        text = _sentence_drop(text, unsupported)
    if not _markers(text):
        text += f" [{named[0]}]"
    return text, violations


def _apply_analysis(statement: ComposedStatement, verdict: StatementConformance | None, ctx: "_Context"):
    text = statement.text.strip()
    violations: list[ConformanceViolation] = []
    if _markers(text) or (verdict is not None and verdict.citations_on_inference):
        violations.append(ConformanceViolation(
            "CITATION_OVERCLAIM", "analysis carried source citation markers as if directly sourced"))
        text = _strip_markers(text, None)

    unsupported = _unsupported_numbers(text, ctx.all_evidence)
    if unsupported:
        violations.append(ConformanceViolation(
            "INVENTED_NUMERIC", f"analysis contains numbers not present in any governed input: {sorted(set(unsupported))}"))
        text = _sentence_drop(text, unsupported)
        if not text.strip():
            return None, violations

    # Material/framing is an open-world judgment owned by the assessor. The cue is a tripwire
    # that can only ADD a framing requirement, never certify safety.
    assessor_material = verdict is not None and verdict.material_conclusion
    assessor_framed = verdict is not None and verdict.framed_as_interpretation
    tripwire = _tripwire(text, _CAUSAL_TRIPWIRES) or _tripwire(text, _CONFIRMATION_TRIPWIRES)
    if not assessor_framed and (assessor_material or tripwire):
        violations.append(ConformanceViolation(
            "UNFRAMED_ANALYSIS", "material analytical conclusion must be framed as interpretation"))
        text = _framed(text, ctx, strip_citations=False)
    return text, violations


def _downgrade_or_canonical(text: str, verdict: StatementConformance | None, valid: list[str], ctx: "_Context") -> str:
    # A failed factual representation is not rescued by relabeling it as analysis.
    return _canonical_for(valid, ctx)


# ------------------------------------------------------------------ structural guarantees


def _ensure_mandatory_facts(rendered: list[str], ctx: "_Context"):
    violations: list[ConformanceViolation] = []
    if not ctx.package.facts:
        return "", violations
    represented = set()
    for text in rendered:
        represented.update(_markers(text))
    missing = [
        fact for fact in ctx.package.facts
        if fact.marker not in represented
    ]
    if not missing:
        return "", violations
    for fact in missing:
        violations.append(ConformanceViolation(
            "MISSING_MANDATORY_FACT", f"approved fact {fact.marker} was not communicated"))
    lines = [f"{' '.join(fact.text.split())} [{fact.marker}]" for fact in missing]
    return "\n".join(lines), violations


def _ensure_qualified_lane(rendered: list[str], ctx: "_Context"):
    violations: list[ConformanceViolation] = []
    if not ctx.package.qualified_evidence:
        return "", violations
    represented = {marker for text in rendered for marker in _markers(text)}
    if set(ctx.qualified_markers) <= represented:
        return "", violations
    violations.append(ConformanceViolation(
        "MISSING_QUALIFIED_LANE", "qualified evidence was dropped from the answer"))
    header = _QUALIFIED_HEADER.get("zh" if ctx.language.startswith("zh") else "en")
    return header + "\n" + render_qualified_evidence(ctx.package), violations


def _ensure_limitations(rendered: list[str], ctx: "_Context"):
    violations: list[ConformanceViolation] = []
    gaps = [limitation for limitation in (ctx.package.limitations or []) if limitation]
    if not gaps and not ctx.package.unresolved:
        return "", violations
    lines = [f"- {gap}" for index, gap in enumerate(ctx.package.limitations, 1)
             if gap and f"L{index}" not in ctx.represented_limitations]
    lines.extend(f"- {entry.statement}" for entry in ctx.package.unresolved
                 if entry.requirement_id not in ctx.represented_requirements)
    if not lines:
        return "", violations
    violations.append(ConformanceViolation("MISSING_LIMITATION", "unresolved requirements were not communicated"))
    header = _NOT_ESTABLISHED_HEADER.get("zh" if ctx.language.startswith("zh") else "en")
    return header + "\n" + "\n".join(lines), violations


# ------------------------------------------------------------------ helpers


@dataclass
class _Context:
    package: AnswerPackage
    language: str
    represented_limitations: set[str] = field(default_factory=set)
    represented_requirements: set[str] = field(default_factory=set)

    @property
    def fact_markers(self) -> dict:
        return {fact.marker: fact for fact in self.package.facts}

    @property
    def qualified_markers(self) -> dict:
        return {f"Q{index}": item for index, item in enumerate(self.package.qualified_evidence, start=1)}

    @property
    def approved_evidence(self) -> list[tuple[str, float]]:
        return _evidence_values([fact.text for fact in self.package.facts])

    @property
    def all_evidence(self) -> list[tuple[str, float]]:
        texts = [fact.text for fact in self.package.facts]
        texts += [item.span_text or "" for item in self.package.qualified_evidence]
        return _evidence_values(texts)

    def evidence_for(self, markers: list[str]) -> list[tuple[str, float]]:
        return _evidence_values([self.fact_markers[m].text for m in markers if m in self.fact_markers])

    def qualified_evidence_for(self, item) -> list[tuple[str, float]]:
        return _evidence_values([item.span_text or ""])


def _markers(text: str) -> list[str]:
    return _MARKER_RE.findall(text or "")


def _declared_markers(statement: ComposedStatement) -> list[str]:
    return [marker.strip("[]") for marker in statement.citations if marker.strip("[]")]


def _strip_markers(text: str, markers: set[str] | None) -> str:
    def replace(match):
        return "" if markers is None or match.group(1) in markers else match.group(0)
    return _MARKER_RE.sub(replace, text or "").strip()


def _tripwire(text: str, cues) -> bool:
    low = (text or "").lower()
    return any(cue in low for cue in cues)


def _material_not_in_premises(text: str, valid: list[str], ctx: "_Context") -> bool:
    cues = _CAUSAL_TRIPWIRES + _CONFIRMATION_TRIPWIRES
    if not _tripwire(text, cues):
        return False
    for marker in valid:
        if _tripwire(ctx.fact_markers[marker].text, cues):
            return False
    return True


def _framed(text: str, ctx: "_Context", *, strip_citations: bool) -> str:
    if strip_citations:
        text = _strip_markers(text, None)
    lead = _ANALYSIS_LEAD.get("zh" if ctx.language.startswith("zh") else "en")
    if text.lower().startswith(lead.lower()):
        return text
    return f"{lead}{text}"


def _canonical_for(markers: list[str], ctx: "_Context") -> str:
    facts = [ctx.fact_markers[marker] for marker in markers if marker in ctx.fact_markers]
    if not facts:
        return ""
    return "\n".join(f"{' '.join(fact.text.split())} [{fact.marker}]" for fact in facts)


def _sentence_drop(text: str, unsupported: list[str]) -> str:
    present = set(unsupported)
    kept = [
        sentence for sentence in _sentences(text)
        if not any(number in present for number in _NUMBER_RE.findall(sentence))
    ]
    return " ".join(kept).strip()


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?。！？])\s+", text or "") if part.strip()]


def _evidence_values(texts: list[str]) -> list[tuple[str, float]]:
    values: list[tuple[str, float]] = []
    for text in texts:
        for number in _NUMBER_RE.findall(text or ""):
            try:
                values.append((number, float(number)))
            except ValueError:
                continue
    return values


def _number_supported(number: str, evidence: list[tuple[str, float]]) -> bool:
    if len(number) == 1 and number.isdigit():
        return True  # citation marker or list index
    if len(number) == 4 and number.isdigit() and 1900 <= int(number) <= 2100:
        return True  # season / scope year
    try:
        target = float(number)
    except ValueError:
        return True
    decimals = len(number.split(".")[1]) if "." in number else 0
    tolerance = 0.5 * (10 ** -decimals) + 1e-9
    return any(abs(value - target) <= tolerance for _, value in evidence)


def _unsupported_numbers(text: str, evidence: list[tuple[str, float]]) -> list[str]:
    return sorted({number for number in _NUMBER_RE.findall(text or "") if not _number_supported(number, evidence)})


def _join(parts: list[str], language: str) -> str:
    cleaned = [part for part in (block.strip() for block in parts) if part]
    if not cleaned:
        return ""
    separator = "" if str(language).startswith("zh") else "\n\n"
    return separator.join(cleaned)
