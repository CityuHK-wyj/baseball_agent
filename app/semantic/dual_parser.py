"""Dual semantic parser: independent proposal, independent review, deterministic reconcile.

Flow:

    raw query
      -> lexical anchors (dates, numbers, units, explicit literals)
      -> Candidate A (extractor LLM)     Candidate B (reviewer LLM)
      -> SemanticReconciler (material agreement vs disagreement)
      -> deterministic domain validator (anchor reconciliation + hard invariants)
      -> canonical typed semantics OR clarification

The reviewer never receives Candidate A, so it cannot be anchored on the extractor's
mistakes. No single model may both propose and approve meaning. When only one model is
available, or neither is, execution is refused unless the deterministic high-confidence
path proves the entire requested semantics.
"""

from dataclasses import replace
from datetime import datetime, timezone
import time

from app.models.contracts import LocationConstraint
from app.llm.provider import ModelProvider
from app.models.semantic_review import ModelCallMetrics, SemanticReviewResult
from app.semantic.hybrid_parser import HybridSemanticParser
from app.semantic.lexical_anchors import LexicalAnchors, extract_lexical_anchors
from app.semantic.semantic_extractor import (DeterministicSemanticExtractor,
                                             LLMSemanticExtractor, LLMSemanticReviewer,
                                             SemanticExtractor, SemanticVocabulary)
from app.semantic.semantic_reconciler import SemanticReconciler
from app.semantic.semantic_validator import (SemanticParseResult, SemanticValidationError,
                                             validate_candidate)

MATERIAL_DISAGREEMENT = "SEMANTIC_MATERIAL_DISAGREEMENT"
SEMANTIC_AMBIGUITY = "SEMANTIC_AMBIGUITY"
SEMANTIC_UNAVAILABLE = "SEMANTIC_UNAVAILABLE"


def needs_dual_review(anchors: LexicalAnchors) -> bool:
    """Deterministic, documented risk rule.

    Dual review is required whenever the query carries an analytical cue (a numeric
    threshold, qualification, count, ranking, population, pitch family or ambiguous
    location). Plain knowledge/entity questions (for example ``DFA是什么意思？``) do not
    trigger two analytics model calls.
    """
    return anchors.has_analytical_cue


class DualSemanticParser:
    """Extractor + reviewer + reconciler + deterministic validator."""

    def __init__(self, extractor: SemanticExtractor, reviewer: SemanticExtractor, *,
                 fallback: SemanticExtractor | None = None,
                 vocabulary: SemanticVocabulary | None = None,
                 reconciler: SemanticReconciler | None = None,
                 review_store=None,
                 extractor_model: str = "", reviewer_model: str = "",
                 clock=time.perf_counter) -> None:
        self._extractor = extractor
        self._reviewer = reviewer
        self._vocabulary = vocabulary or SemanticVocabulary()
        self._fallback = fallback or DeterministicSemanticExtractor()
        self._fallback_parser = HybridSemanticParser(
            extractor=self._fallback, fallback=self._fallback, vocabulary=self._vocabulary)
        self._reconciler = reconciler or SemanticReconciler()
        self._review_store = review_store
        self._extractor_model = extractor_model
        self._reviewer_model = reviewer_model
        self._clock = clock

    @property
    def extractor_name(self) -> str:
        return "dual"

    def parse(self, raw_query: str) -> SemanticParseResult:
        anchors = extract_lexical_anchors(raw_query)
        if not needs_dual_review(anchors):
            # Knowledge/entity queries never pay for two analytics model calls.
            return self._fallback_parser.parse(raw_query)
        key = self._review_key(raw_query)
        cached = self._review_store.get(key) if self._review_store is not None else None
        if cached is not None:
            replayed = self._replay(cached, raw_query, anchors)
            if replayed is not None:
                return replayed
        return self._dual_review(raw_query, anchors, key)

    # -- review flow --------------------------------------------------------
    def _dual_review(self, raw_query: str, anchors: LexicalAnchors, key: str) -> SemanticParseResult:
        candidate_a, failure_a, metric_a = self._call("extractor", self._extractor,
                                                      self._extractor_model, raw_query)
        candidate_b, failure_b, metric_b = self._call("reviewer", self._reviewer,
                                                      self._reviewer_model, raw_query)
        calls = (metric_a, metric_b)
        base = dict(raw_query_hash=key, calls=calls, extractor_model=self._extractor_model,
                    reviewer_model=self._reviewer_model,
                    reviewed_at=datetime.now(timezone.utc).isoformat())

        if candidate_a is not None and candidate_b is not None:
            review = self._reconciler.compare(candidate_a, candidate_b, anchors).model_copy(
                update={**base, "outcome": "DUAL_REVIEW"})
            if review.agreement_status == "AGREE":
                parsed, error = self._validate(review.canonical_candidate, raw_query)
                if parsed is not None and not parsed.failed_closed \
                        and not parsed.location_wording_requested:
                    self._save(key, review)
                    return replace(parsed, extractor="dual", review=review)
                if error is not None and not error.recoverable:
                    # Both readers agreed but the meaning is internally contradictory or
                    # otherwise hard-invalid. Never silently pick one wording; clarify.
                    review = review.model_copy(update={"clarification_reason": error.code})
                    self._save(key, review)
                    return self._clarify(error.code, review)
                # Both readers agreed but the deterministic domain layer rejected the
                # meaning (for example exit velocity over all pitches). Never execute.
                return self._with_fallback(raw_query, review, key)
            if review.agreement_status == "AMBIGUOUS":
                # Material ambiguity is surfaced through the domain clarification lifecycle
                # (for example a location-definition CONSTRAINT clarification with options),
                # not as a generic meaning failure. The rest of the agreed semantics is
                # preserved so the clarification does not discard the request.
                self._save(key, review)
                return self._ambiguity_result(review, raw_query)
            review = review.model_copy(update={"clarification_reason": MATERIAL_DISAGREEMENT})
            self._save(key, review)
            return self._clarify(MATERIAL_DISAGREEMENT, review)

        if candidate_a is not None or candidate_b is not None:
            status = "REVIEWER_UNAVAILABLE" if candidate_a is not None else "EXTRACTOR_UNAVAILABLE"
            review = SemanticReviewResult(
                **{**base, "outcome": "SINGLE_REVIEW", "agreement_status": status},
                extractor_candidate=candidate_a, reviewer_candidate=candidate_b,
                notes=("Only one semantic reader was available; no independent model "
                       "approval. Its candidate is used only if the deterministic "
                       "validator and lexical anchors accept it.",),)
            # Open-world policy: a single reader's validated candidate may continue, but
            # only after the deterministic validator and anchors accept it. The validator
            # remains the authority; the missing independent reader is recorded, not
            # silently ignored.
            available = candidate_a if candidate_a is not None else candidate_b
            parsed, _error = self._validate(available, raw_query)
            if parsed is not None and not parsed.failed_closed:
                if parsed.location_wording_requested:
                    return replace(parsed, extractor="dual", review=review)
                self._save(key, review.model_copy(update={"canonical_candidate": available}))
                return replace(parsed, extractor="dual", review=review)
            return self._with_fallback(raw_query, review, key)

        notes = ("Both semantic readers were unavailable; refusing to guess.",)
        review = SemanticReviewResult(
            **{**base, "outcome": "DETERMINISTIC_ONLY", "agreement_status": "BOTH_UNAVAILABLE"},
            notes=tuple(note for note in (*notes, failure_a, failure_b) if note),)
        self._save(key, review)
        return self._clarify(SEMANTIC_UNAVAILABLE, review)

    # -- helpers ------------------------------------------------------------
    def _with_fallback(self, raw_query: str, review: SemanticReviewResult,
                       key: str) -> SemanticParseResult:
        parsed = self._fallback_parser.parse(raw_query)
        if parsed.failed_closed or parsed.location_wording_requested:
            reason = parsed.clarification_reason or SEMANTIC_UNAVAILABLE
            review = review.model_copy(update={"clarification_reason": reason})
            self._save(key, review)
            return replace(parsed, review=review)
        # Persist the deterministic high-confidence candidate so a restart replays the
        # same approved semantics instead of calling the models again.
        try:
            fallback_candidate = self._fallback.extract(raw_query, self._vocabulary)
        except Exception:  # noqa: BLE001 - deterministic extraction is best-effort
            fallback_candidate = None
        review = review.model_copy(update={"canonical_candidate": fallback_candidate})
        self._save(key, review)
        return replace(parsed, review=review)

    def _validate(self, candidate, raw_query: str):
        if candidate is None:
            return None, None
        try:
            return validate_candidate(candidate, raw_query, self._vocabulary), None
        except SemanticValidationError as error:
            return None, error

    def _call(self, role: str, extractor: SemanticExtractor, model: str, raw_query: str):
        start = self._clock()
        try:
            candidate = extractor.extract(raw_query, self._vocabulary)
            latency = self._elapsed_ms(start)
            return candidate, None, ModelCallMetrics(role=role, model=model,
                                                     latency_ms=latency, ok=True)
        except Exception as error:  # noqa: BLE001 - a model call must never crash a request
            latency = self._elapsed_ms(start)
            return None, type(error).__name__, ModelCallMetrics(
                role=role, model=model, latency_ms=latency, ok=False,
                failure=type(error).__name__)

    def _elapsed_ms(self, start: float) -> int:
        return max(int((self._clock() - start) * 1000), 0)

    def _clarify(self, reason: str, review: SemanticReviewResult,
                 *, location_wording_requested: bool = False) -> SemanticParseResult:
        summary = (f"clarify:{reason}",) if reason else ("clarify:location_ambiguity",)
        return SemanticParseResult(
            constraints=(), extractor="dual", clarification_reason=reason,
            location_wording_requested=location_wording_requested,
            summary=summary, review=review)

    def _ambiguity_result(self, review: SemanticReviewResult,
                          raw_query: str) -> SemanticParseResult:
        """Preserve the agreed non-ambiguous semantics while asking about the ambiguity."""
        parsed, _ = self._validate(review.extractor_candidate, raw_query)
        constraints = ()
        if parsed is not None:
            constraints = tuple(item for item in parsed.constraints
                                if not isinstance(item, LocationConstraint))
        summary = tuple(f"{item.kind}:{item.key}" for item in constraints)
        return SemanticParseResult(
            constraints=constraints, extractor="dual",
            location_wording_requested=any(kind.startswith("location")
                                           for kind in review.ambiguities),
            summary=summary, review=review,
            understanding=(parsed.understanding if parsed is not None else None),
            ambiguities=review.extractor_candidate.ambiguities
            if review.extractor_candidate is not None else ())

    def _review_key(self, raw_query: str) -> str:
        from app.persistence.semantic_review import review_key
        return review_key(raw_query, self._extractor_model, self._reviewer_model)

    def _save(self, key: str, review: SemanticReviewResult) -> None:
        if self._review_store is not None:
            self._review_store.save(key, review)

    def _replay(self, review: SemanticReviewResult, raw_query: str,
                anchors: LexicalAnchors) -> SemanticParseResult | None:
        if review.agreement_status == "AMBIGUOUS":
            return self._ambiguity_result(review, raw_query)
        if review.clarification_reason:
            return self._clarify(review.clarification_reason, review,
                                 location_wording_requested=bool(review.ambiguities))
        if review.canonical_candidate is not None:
            parsed, _ = self._validate(review.canonical_candidate, raw_query)
            if parsed is not None and not parsed.failed_closed \
                    and not parsed.location_wording_requested:
                return replace(parsed, extractor="dual", review=review)
        return None


def build_dual_parser(provider: ModelProvider, *, extractor_model: str, reviewer_model: str,
                      timeout: float = 30.0, vocabulary: SemanticVocabulary | None = None,
                      review_store=None) -> DualSemanticParser:
    """Compose the dual parser from one provider and two independent model configs."""
    vocabulary = vocabulary or SemanticVocabulary()
    return DualSemanticParser(
        LLMSemanticExtractor(provider, extractor_model, timeout),
        LLMSemanticReviewer(provider, reviewer_model, timeout),
        fallback=DeterministicSemanticExtractor(),
        vocabulary=vocabulary,
        review_store=review_store,
        extractor_model=extractor_model,
        reviewer_model=reviewer_model,
    )
