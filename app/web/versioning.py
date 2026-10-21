"""Raw response → extracted body → span version/offset contract (M1-T6, ADR 0027 §6.1).

Three identities, never conflated:

1. raw response bytes (``raw_content_hash``, existing ``content_hash``);
2. extracted ``body_text`` (``body_hash``, ``extractor_version``);
3. ``EvidenceSpan`` offsets, valid only for the body version they were created from.

M1 records the metadata and enforces the version-match rule in tests; no cache and no
span-selection change is introduced.
"""

from __future__ import annotations

import hashlib
from typing import Any, Callable, Mapping

EXTRACTOR_VERSION = "beautifulsoup-v1"


def body_hash(body_text: str) -> str:
    """SHA-256 of the extracted body text (not of the raw response bytes)."""
    return hashlib.sha256((body_text or "").encode("utf-8")).hexdigest()


def body_metadata(body_text: str, *, extractor_version_value: str = EXTRACTOR_VERSION) -> dict[str, str]:
    return {"body_hash": body_hash(body_text), "extractor_version": extractor_version_value}


def can_reuse_offsets(
    metadata: Mapping[str, Any] | None,
    body_text: str,
    *,
    extractor_version_value: str = EXTRACTOR_VERSION,
) -> bool:
    """True only when a persisted span's body version matches this extraction.

    A changed extractor version invalidates offsets even when the text is byte-identical.
    """
    if not metadata:
        return False
    return (
        metadata.get("body_hash") == body_hash(body_text)
        and metadata.get("extractor_version") == extractor_version_value
    )


def reextract(
    metadata: Mapping[str, Any] | None,
    raw_bytes: bytes | None,
    extract: Callable[[bytes], str],
    *,
    extractor_version_value: str = EXTRACTOR_VERSION,
) -> tuple[str, str]:
    """Re-extract a body for a different ``(body_hash, extractor_version)``.

    Always returns a freshly extracted body and the current extractor version so callers
    cannot silently reuse stale offsets when the version (or the text) changed.
    """
    body = extract(raw_bytes or b"")
    return body, extractor_version_value
