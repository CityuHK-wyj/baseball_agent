"""Hybrid semantic parser: extractor proposes, validator decides, fallback stays safe.

Flow:

    raw query
      -> extractor (LLM or deterministic) -> SemanticCandidate
      -> deterministic validator -> canonical typed constraints + provenance
      -> clarification / fail-closed when the candidate cannot be trusted

If the primary extractor is unavailable or produces an ungrounded proposal, the parser
falls back to the high-confidence deterministic extractor and records why. A provider
failure can never *silently* drop an explicit user constraint: when the fallback cannot
prove the supported semantics and the query clearly asks for analytics, the parser fails
closed into clarification instead of running a weaker interpretation.
"""

from app.semantic import analytics_intent as _ai
from app.semantic.semantic_extractor import (DeterministicSemanticExtractor,
                                             SemanticExtractionError, SemanticExtractor,
                                             SemanticVocabulary)
from app.semantic.semantic_validator import (PARSER_VERSION, SemanticParseResult,
                                             SemanticValidationError, validate_candidate)


class HybridSemanticParser:
    def __init__(self, extractor: SemanticExtractor | None = None,
                 fallback: SemanticExtractor | None = None,
                 vocabulary: SemanticVocabulary | None = None,
                 parser_version: str = PARSER_VERSION) -> None:
        self._primary = extractor or DeterministicSemanticExtractor()
        self._fallback = fallback or DeterministicSemanticExtractor()
        self._vocabulary = vocabulary or SemanticVocabulary()
        self._parser_version = parser_version

    @property
    def extractor_name(self) -> str:
        return self._primary.name

    def parse(self, raw_query: str) -> SemanticParseResult:
        try:
            candidate = self._primary.extract(raw_query, self._vocabulary)
        except SemanticExtractionError as error:
            return self._fallback_parse(raw_query, f"extractor={type(error).__name__}")
        try:
            return validate_candidate(candidate, raw_query, self._vocabulary)
        except SemanticValidationError as error:
            if not error.recoverable:
                return self._clarify(candidate.extractor, error.code, error.detail)
            return self._fallback_parse(raw_query, f"validation={error.code}")

    def _fallback_parse(self, raw_query: str, reason: str) -> SemanticParseResult:
        try:
            candidate = self._fallback.extract(raw_query, self._vocabulary)
        except SemanticExtractionError:
            return self._clarify(self._fallback.name, "SEMANTIC_UNAVAILABLE",
                                 "semantic parsing is unavailable")
        try:
            result = validate_candidate(candidate, raw_query, self._vocabulary,
                                        fallback_reason=reason)
        except SemanticValidationError as error:
            return self._clarify(self._fallback.name, error.code, error.detail)
        if not result.constraints and self._has_analytical_cue(raw_query):
            # The query asks for analytics but neither extractor could prove it; do not
            # silently answer a different question.
            return self._clarify(self._fallback.name, "SEMANTIC_UNAVAILABLE",
                                 "explicit analytical constraints could not be parsed safely")
        return result

    def _clarify(self, extractor: str, code: str, detail: str) -> SemanticParseResult:
        return SemanticParseResult(
            constraints=(), extractor=extractor, parser_version=self._parser_version,
            clarification_reason=code, summary=(f"clarify:{code}",))

    @staticmethod
    def _has_analytical_cue(raw_query: str) -> bool:
        return bool(_ai.extract_analytical_constraints(raw_query).constraints)
