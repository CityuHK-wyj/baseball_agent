"""Bounded expression of Judge-accepted mandatory results; no evidence decisions."""
from app.contracts.models import CoverageState, EpistemicStatus, PropositionKind, ReferenceType

COMMUNICABLE_KINDS = {
    PropositionKind.VALUE, PropositionKind.FACT, PropositionKind.EVENT,
    PropositionKind.COMPARISON, PropositionKind.EXPLANATION, PropositionKind.GROUNDED,
}
RESULT_HEADER = '[已验证结果 / Verified results]'
_MARKER = {EpistemicStatus.SUPPORTED:'S', EpistemicStatus.INFERRED:'I',
           EpistemicStatus.BACKGROUND:'B', EpistemicStatus.HYPOTHESIS:'H',
           EpistemicStatus.UNKNOWN:'U'}

# Source types whose reference span carries the approved factual substance.
_SPAN_BEARING = {ReferenceType.WEB_EVIDENCE, ReferenceType.KNOWLEDGE_ITEM}


def claim_factual_text(claim) -> str:
    """The approved factual substance of a claim, without source-ordering metadata.

    The Judge records the accepted EvidenceSpan text on the support reference. The claim's
    human text is source-oriented (title/url/retrieved), which must not leak into the answer.
    """
    spans: list[str] = []
    for ref in claim.support_refs:
        span = (ref.span or "").strip()
        if span and ref.ref_type in _SPAN_BEARING:
            spans.append(span)
    if spans:
        return ' '.join(dict.fromkeys(spans))
    return (claim.text or "").strip()


def mandatory_claims(projection):
    wanted = {p.id for p in projection.propositions if p.mandatory
              and p.coverage == CoverageState.SATISFIED and p.kind in COMMUNICABLE_KINDS}
    return [(index, claim) for index, claim in enumerate(projection.claims, 1)
            if wanted.intersection(claim.proposition_ids)]


def render_qualified_evidence(projection, *, max_chars=4000):
    """Deterministic rendering of qualified evidence, always with its structural limitation.

    This never states a qualified item as an approved fact; it exists so a bounded, non-LLM
    answer cannot silently drop the limitation (WSA-03/presentation).
    """
    lines = []
    remaining = max_chars
    for index, item in enumerate(getattr(projection, "qualified_evidence", []) or [], start=1):
        text = " ".join(str(getattr(item, "span_text", "") or "").split())
        if not text:
            continue
        source = str(getattr(item, "source_url", "") or getattr(item, "source_title", "") or "")
        limitation = " ".join(str(getattr(item, "limitation", "") or "").split())
        line = f"[Q{index}] {text}"
        if source:
            line += f" (source: {source})"
        if limitation:
            line += f" — not established: {limitation}"
        if len(line) + 1 <= remaining:
            lines.append(line)
            remaining -= len(line) + 1
    return "\n".join(lines)


def render_accepted_results(projection, *, max_chars=16000):
    """Render the approved factual substance, keeping polarity, scope and source numbering.

    This is the bounded deterministic answer used when the composer model is unavailable and
    as the consistency repair. Each approved fact is stated as a sentence with its citation;
    source metadata (title/url/retrieved) is never rendered.
    """
    lines = []
    remaining = max_chars
    for index, claim in mandatory_claims(projection):
        text = ' '.join(claim_factual_text(claim).split())
        line = f"{text} [{_MARKER[claim.epistemic]}{index}]"
        if text and len(line) + 1 <= remaining:
            lines.append(line)
            remaining -= len(line) + 1
    return '\n'.join(lines)
