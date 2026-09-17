"""LLM Evidence Extractor behind the EvidenceExtractor Protocol.

Structured output is validated; malformed output falls back to the deterministic
extractor. The raw document is still required: the LLM never invents a source.
"""

from app.llm.parsing import parse_json_object
from app.llm.prompts import EVIDENCE_PROMPT, PromptTemplate
from app.llm.provider import ModelProvider, ProviderError
from app.models.artifacts import Provenance
from app.models.evidence import Evidence, EvidenceClaim, RawWebResult
from app.semantic.evidence import EvidenceExtractor

_MAX_TEXT = 4000
_MAX_CLAIMS = 32
_MAX_CLAIM_LENGTH = 1_000
_ALLOWED_TOP_LEVEL_FIELDS = frozenset({"claims"})
_ALLOWED_CLAIM_FIELDS = frozenset({"claim", "support", "claim_time"})


class LLMEvidenceExtractor:
    def __init__(self, provider: ModelProvider, model: str,
                 prompt: PromptTemplate = EVIDENCE_PROMPT,
                 fallback: EvidenceExtractor | None = None, timeout: float = 30.0) -> None:
        self._provider = provider
        self._model = model
        self._prompt = prompt
        self._fallback = fallback
        self._timeout = timeout

    def extract(self, raw: RawWebResult) -> Evidence:
        try:
            prompt = self._prompt.render(url=raw.url, title=raw.title, text=raw.text[:_MAX_TEXT])
            response = self._provider.complete(prompt, model=self._model, timeout=self._timeout)
            return self._parse(raw, response.text)
        except (ProviderError, ValueError, KeyError):
            if self._fallback is not None:
                return self._fallback.extract(raw)
            raise

    @staticmethod
    def _parse(raw: RawWebResult, text: str) -> Evidence:
        data = parse_json_object(text)
        unknown_fields = set(data) - _ALLOWED_TOP_LEVEL_FIELDS
        if unknown_fields:
            raise ValueError(f"Evidence output contains unknown field(s): {sorted(unknown_fields)}")
        items = data.get("claims")
        if not isinstance(items, list):
            raise ValueError("Evidence output claims must be an array")
        if len(items) > _MAX_CLAIMS:
            raise ValueError(f"Evidence output exceeds {_MAX_CLAIMS} claims")

        claims = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("Each evidence claim must be an object")
            unknown_fields = set(item) - _ALLOWED_CLAIM_FIELDS
            if unknown_fields:
                raise ValueError(f"Evidence claim contains unknown field(s): {sorted(unknown_fields)}")
            claim = item.get("claim")
            support = item.get("support")
            claim_time = item.get("claim_time", "")
            if not isinstance(claim, str) or not claim.strip():
                raise ValueError("Evidence claim must be a non-empty string")
            if not isinstance(support, str) or not support.strip():
                raise ValueError("Evidence support must be a non-empty string")
            if not isinstance(claim_time, str):
                raise ValueError("Evidence claim_time must be a string")
            claim = claim.strip()
            support = support.strip()
            if len(claim) > _MAX_CLAIM_LENGTH or len(support) > _MAX_CLAIM_LENGTH:
                raise ValueError("Evidence claim or support exceeds the size limit")
            if claim not in raw.text or support not in raw.text:
                raise ValueError("Evidence claim and support must be copied from the raw document")
            claims.append(EvidenceClaim(claim=claim, support=support, claim_time=claim_time.strip()))
        return Evidence(
            evidence_id=f"evidence-{raw.result_id}", raw_ref=raw.result_id, url=raw.url,
            title=raw.title, claims=tuple(claims),
            provenance=Provenance(source=raw.source, source_kind="WEB", reference=raw.url,
                                  retrieved_at=raw.retrieved_at))
