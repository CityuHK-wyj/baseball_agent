"""Durable semantic-review records.

A reviewed query is deterministic for a fixed parser configuration, so a restart (or a
repeated request) must reuse the already-approved reconciled semantics instead of calling
the models again. This store is the durable side of that rule; it never holds prompts,
raw model text or credentials.
"""

import hashlib

from app.models.semantic_review import SemanticReviewResult
from app.persistence.store import OperationalStore

_SENTINEL_RUN = "semantic-review"


def review_key(raw_query: str, extractor_model: str, reviewer_model: str) -> str:
    identity = "\x1f".join((raw_query.strip(), extractor_model, reviewer_model))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


class SemanticReviewStore:
    """Thin adapter over the operational store for semantic-review records."""

    def __init__(self, store: OperationalStore) -> None:
        self._store = store

    def get(self, key: str) -> SemanticReviewResult | None:
        record = self._store.get_object("semantic_review", key)
        if record is None:
            return None
        return SemanticReviewResult.model_validate(record.payload)

    def save(self, key: str, result: SemanticReviewResult) -> SemanticReviewResult:
        record = self._store.save_object("semantic_review", key, _SENTINEL_RUN,
                                         result.model_dump(mode="json"))
        return SemanticReviewResult.model_validate(record.payload)
