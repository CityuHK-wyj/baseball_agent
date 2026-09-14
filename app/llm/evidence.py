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
        claims = []
        for item in data.get("claims", []):
            claim = str(item["claim"]).strip()
            if not claim:
                raise ValueError("Evidence claim must not be empty")
            claims.append(EvidenceClaim(claim=claim, support=str(item.get("support", "")),
                                        claim_time=str(item.get("claim_time", ""))))
        return Evidence(
            evidence_id=f"evidence-{raw.result_id}", raw_ref=raw.result_id, url=raw.url,
            title=raw.title, claims=tuple(claims),
            provenance=Provenance(source=raw.source, source_kind="WEB", reference=raw.url,
                                  retrieved_at=raw.retrieved_at))
