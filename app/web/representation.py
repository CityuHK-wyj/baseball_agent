"""Content-artifact representations: preserve first, interpret lazily (ADR 0031).

The open web is an open-world input boundary. Rather than forcing every resource into one
exhaustive semantic schema, Web Research acquires whatever views a resource offers and keeps
their native structure:

    URL / API / feed / document
      -> acquire available content
      -> preserve the original representation
      -> expose optional derived representations
      -> derive further views only when the current task asks for them
      -> extract evidence from the appropriate representation

Nothing here fabricates a missing field. A representation with no author has no author; a
derived view records ``derived_from``/``derivation_method``/``derived_by`` so a later
extractor can always return to its source. Representation kind is a content-access fact and
never a trust signal.
"""

from __future__ import annotations

import base64
import hashlib
import json
import xml.etree.ElementTree as ET
from typing import Any, Iterable

from app.contracts.models import Representation, RepresentationKind

# A bounded inline payload keeps raw content reproducible without unbounded memory. Larger
# payloads keep their content address (``content_ref``) and are re-fetchable/replayable.
_MAX_INLINE_BYTES = 200_000


def _sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _decode(raw: bytes) -> str:
    if not raw:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-8", "replace")


def canonical_json(value: Any) -> str:
    """The exact, replayable text of a structured value (stable key order, no whitespace)."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _escaping(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def resolve_json_pointer(data: Any, pointer: str) -> Any:
    """Resolve an RFC 6901 JSON Pointer; raises ``KeyError``/``ValueError`` when unresolvable."""
    if pointer in ("", "/"):
        return data
    if not pointer.startswith("/"):
        raise ValueError(f"invalid JSON pointer {pointer!r}")
    current = data
    for token in pointer.lstrip("/").split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(token)]
        elif isinstance(current, dict):
            current = current[token]
        else:  # pragma: no cover - malformed pointer against scalar
            raise KeyError(token)
    return current


def iter_structured_records(data: Any) -> Iterable[tuple[str, Any]]:
    """Yield ``(pointer, record)`` for the records inside a native structured payload.

    Top-level objects and the items of a top-level/array-valued collection are records. The
    pointer is stable and representation-specific, so structured evidence never needs
    artificial prose offsets.
    """
    if isinstance(data, dict):
        yield "", data
        for key, value in data.items():
            if isinstance(value, list):
                for index, item in enumerate(value):
                    if isinstance(item, (dict, list)):
                        yield f"/{_escaping(str(key))}/{index}", item
    elif isinstance(data, list):
        for index, item in enumerate(data):
            if isinstance(item, dict):
                yield f"/{index}", item


def _native_kind(media_type: str, retrieval_method: str) -> RepresentationKind:
    lowered = (media_type or "").lower()
    if retrieval_method == "rss_item":
        return RepresentationKind.FEED_ITEM
    if retrieval_method == "browser":
        return RepresentationKind.DOM
    if "json" in lowered:
        return RepresentationKind.JSON
    if "html" in lowered or "xhtml" in lowered:
        return RepresentationKind.HTML
    if "xml" in lowered or "rss" in lowered or "atom" in lowered:
        return RepresentationKind.XML
    return RepresentationKind.RAW


def _parse_native(kind: RepresentationKind, raw: bytes) -> Any:
    if not raw:
        return None
    try:
        if kind == RepresentationKind.JSON:
            return json.loads(raw.decode("utf-8", "replace"))
        if kind in {RepresentationKind.XML, RepresentationKind.FEED_ITEM}:
            return _xml_to_dict(ET.fromstring(raw))
    except Exception:  # noqa: BLE001 - a malformed payload is preserved verbatim, not guessed
        return None
    return None


def _xml_to_dict(element: ET.Element) -> dict[str, Any]:
    """A lossless-enough native XML view (attributes + text + children); never invents fields."""
    value: dict[str, Any] = {"tag": element.tag}
    if element.attrib:
        value["attributes"] = dict(element.attrib)
    text = (element.text or "").strip()
    if text:
        value["text"] = text
    children = [_xml_to_dict(child) for child in list(element)]
    if children:
        value["children"] = children
    return value


def _inline(raw: bytes) -> str:
    if len(raw) <= _MAX_INLINE_BYTES:
        return _decode(raw)
    # A binary/large payload is referenced, not silently mis-decoded into the inline slot.
    if raw and _looks_binary(raw):
        return base64.b64encode(raw).decode("ascii")
    return _decode(raw[:_MAX_INLINE_BYTES])


def _looks_binary(raw: bytes) -> bool:
    sample = raw[:2048]
    return b"\x00" in sample


def build_representations(page: Any, *, extractor_version: str) -> list[Representation]:
    """Build the representation set for one acquired page.

    Always yields a ``TEXT`` representation for the extracted body so the existing
    ``body_text``/offset evidence path keeps working. When the acquisition retained raw
    content, a native representation (and a ``RAW`` one) is preserved as well, and the text
    representation records its derivation lineage.
    """
    raw: bytes = bytes(getattr(page, "raw_content", b"") or b"")
    media_type: str = str(getattr(page, "content_type", "") or "")
    method: str = str(getattr(page, "retrieval_method", "http") or "http")
    body: str = str(getattr(page, "text", "") or "")

    representations: list[Representation] = []
    native: Representation | None = None

    if raw:
        kind = _native_kind(media_type, method)
        native = Representation(
            kind=kind,
            media_type=media_type or "application/octet-stream",
            content=_inline(raw),
            content_hash=hashlib.sha256(raw).hexdigest(),
            acquisition_method=method or "http",
            derivation_method="acquired-v1",
            derived_by="source",
            structured_data=_parse_native(kind, raw),
            locator_type="json_pointer" if kind in {RepresentationKind.JSON, RepresentationKind.XML} else "char_offsets",
            provenance={"final_url": getattr(page, "final_url", "")},
        )
        native.content_ref = f"content:sha256:{native.content_hash}"
        representations.append(native)
        if kind not in {RepresentationKind.RAW, RepresentationKind.HTML, RepresentationKind.DOM}:
            # Keep the raw acquisition itself available even when a typed view exists.
            raw_rep = Representation(
                kind=RepresentationKind.RAW,
                media_type=media_type or "application/octet-stream",
                content=_inline(raw),
                content_hash=native.content_hash,
                acquisition_method=method or "http",
                derivation_method="acquired-v1",
                derived_by="source",
                content_ref=native.content_ref,
            )
            representations.insert(0, raw_rep)

    parent = native.id if native is not None else None
    text_hash = _sha256_text(body)
    text_rep = Representation(
        id=("rep_text_" + text_hash[:16]) if body else "rep_text_empty",
        kind=RepresentationKind.TEXT,
        media_type="text/plain",
        content=body,
        content_hash=text_hash,
        content_ref=f"content:sha256:{text_hash}",
        acquisition_method="derived" if parent else (method or "http"),
        derived_from=parent,
        derivation_method=extractor_version if parent else "acquired-v1",
        derived_by="deterministic" if parent else "source",
        provenance={"extractor_version": extractor_version},
    )
    if not any(item.id == text_rep.id for item in representations):
        representations.append(text_rep)
    return representations


def derive_text_representation(
    document: Any,
    parent: Representation,
    *,
    method: str,
    text: str | None = None,
) -> Representation:
    """Derive a text view from a parent representation without mutating the parent.

    ``text`` defaults to the parent's own content, which is the explicit, traceable
    ``Representation -> TEXT`` step for a native HTML/JSON/XML payload.
    """
    body = parent.content if text is None else text
    content_hash = _sha256_text(body)
    return Representation(
        kind=RepresentationKind.TEXT,
        media_type="text/plain",
        content=body,
        content_hash=content_hash,
        content_ref=f"content:sha256:{content_hash}",
        acquisition_method="derived",
        derived_from=parent.id,
        derivation_method=method,
        derived_by="deterministic",
        provenance={"parent": parent.id, "document_id": getattr(document, "id", "")},
    )


def derive_structured_view(
    document: Any,
    parent: Representation,
    *,
    method: str,
    pointer: str = "",
    derived_by: str = "deterministic",
) -> Representation | None:
    """Derive a structured record view from a parent, or ``None`` when it does not resolve."""
    data = parent.structured_data
    if data is None:
        return None
    try:
        value = resolve_json_pointer(data, pointer)
    except (KeyError, ValueError, IndexError):
        return None
    text = canonical_json(value)
    content_hash = _sha256_text(text)
    return Representation(
        kind=RepresentationKind.STRUCTURED,
        media_type="application/json",
        content=text,
        content_hash=content_hash,
        content_ref=f"content:sha256:{content_hash}",
        acquisition_method="derived",
        derived_from=parent.id,
        derivation_method=method,
        derived_by=derived_by,
        structured_data=value,
        locator_type="json_pointer",
        provenance={"parent": parent.id, "pointer": pointer, "document_id": getattr(document, "id", "")},
    )


def structured_evidence_spans(document: Any, *, max_records: int = 6, max_chars: int = 8000) -> list[dict[str, Any]]:
    """Persistable span dictionaries for a document's native structured representations.

    Each span names the concrete representation and a stable JSON Pointer. The span text is
    the canonical serialization of the located record, so ``resolve(pointer) == span.text``
    is an exact, replayable check -- structured data is never flattened into prose. Oversized
    records are skipped rather than truncated, because a truncated span would not replay.
    """
    spans: list[dict[str, Any]] = []
    for representation in getattr(document, "representations", []) or []:
        if representation.structured_data is None:
            continue
        if representation.kind not in {RepresentationKind.JSON, RepresentationKind.XML,
                                       RepresentationKind.STRUCTURED, RepresentationKind.FEED_ITEM}:
            continue
        for pointer, record in iter_structured_records(representation.structured_data):
            text = canonical_json(record)
            if not text or len(text) > max_chars:
                continue
            spans.append({
                "document_id": getattr(document, "id", ""),
                "representation_id": representation.id,
                "text": text,
                "start": None,
                "end": None,
                "location": f"json:{representation.id}{pointer or '/'}",
                "source_type": "STRUCTURED",
                "retrieval_status": "OK",
                "locator_type": "json_pointer",
                "locator": pointer,
                "quality": "structured",
                "score": 1.0,
                "required_matched": [],
            })
            if len(spans) >= max_records:
                return spans
    return spans


def representation_index(document: Any) -> list[dict[str, Any]]:
    """A progressive-disclosure summary: what views exist, without loading their payload."""
    representatives = _get(document, "representations", []) or []
    index: list[dict[str, Any]] = []
    for representation in representatives:
        if isinstance(representation, dict):
            kind = representation.get("kind", "")
            kind = kind.value if hasattr(kind, "value") else str(kind)
            index.append({
                "representation_id": representation.get("id", ""),
                "kind": kind,
                "media_type": representation.get("media_type", ""),
                "content_ref": representation.get("content_ref", ""),
                "content_hash": representation.get("content_hash", ""),
                "derived_from": representation.get("derived_from"),
                "derived_by": representation.get("derived_by", "source"),
                "derivation_method": representation.get("derivation_method", ""),
                "characters": len(representation.get("content") or ""),
                "has_structured_data": representation.get("structured_data") is not None,
                "locator_type": representation.get("locator_type", ""),
            })
            continue
        index.append({
            "representation_id": representation.id,
            "kind": representation.kind.value if hasattr(representation.kind, "value") else str(representation.kind),
            "media_type": representation.media_type,
            "content_ref": representation.content_ref,
            "content_hash": representation.content_hash,
            "derived_from": representation.derived_from,
            "derived_by": representation.derived_by,
            "derivation_method": representation.derivation_method,
            "characters": len(representation.content or ""),
            "has_structured_data": representation.structured_data is not None,
            "locator_type": representation.locator_type,
        })
    return index


def content_artifact_summary(document: Any) -> dict[str, Any]:
    """A context-budget-safe artifact summary (no representation payloads)."""
    return {
        "artifact_id": _get(document, "id", ""),
        "source_uri": _get(document, "source_uri", "") or _get(document, "canonical_url", "") or _get(document, "final_url", ""),
        "initial_url": _get(document, "initial_url", ""),
        "retrieved_at": _get(document, "retrieved_at", ""),
        "retrieval_status": _get(document, "retrieval_status", ""),
        "source_type": _get(document, "source_type", ""),
        "source_quality": _get(document, "source_quality", ""),
        "primary_representation_id": _get(document, "primary_representation_id", ""),
        "representations": representation_index(document),
    }


def _get(obj: Any, name: str, default: Any) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)
