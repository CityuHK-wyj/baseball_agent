"""Structural evidence integrity; no semantic re-verification."""
from app.contracts.models import EvidenceSpan, RetrievedDocument
import hashlib
import json


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def proposition_signature(proposition):
    fields = ("subject", "entity_id", "namespace", "kind", "time_scope", "population", "required_information", "operation", "statement")
    return text_hash(json.dumps(proposition.model_dump(mode="json", include=set(fields)), sort_keys=True, ensure_ascii=False))


def span_resolves(span: EvidenceSpan, document: RetrievedDocument | None) -> bool:
    if document is None or document.id != span.document_id:
        return False
    if not span.is_admissible() or document.retrieval_status != "OK":
        return False
    if not document.url or not document.retrieved_at or not document.content_hash:
        return False
    if span.source_type.value == "STRUCTURED" or span.locator_type == "json_pointer":
        return _structured_span_resolves(span, document)
    representation = document.representation(span.representation_id) if span.representation_id else None
    body = representation.content if representation is not None else (document.body_text or document.text_excerpt)
    return bool(span.text and span.offsets_are_consistent() and
                span.end <= len(body) and body[span.start:span.end] == span.text)


def _structured_span_resolves(span: EvidenceSpan, document: RetrievedDocument) -> bool:
    """Replay a structured span against the concrete representation it names.

    The stored span text must equal the canonical serialization of the value its JSON
    Pointer locates, so a structured locator is as inspectable as a text offset and cannot
    be fabricated from a bare string.
    """
    from app.web.representation import canonical_json, resolve_json_pointer

    representation = None
    for item in document.representations or []:
        if item.id == span.representation_id:
            representation = item
            break
    if representation is None or representation.structured_data is None:
        return False
    try:
        value = resolve_json_pointer(representation.structured_data, span.locator)
    except (KeyError, ValueError, IndexError):
        return False
    return bool(span.text) and canonical_json(value) == span.text
