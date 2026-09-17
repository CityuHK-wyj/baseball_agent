"""Live LLM semantic extraction evidence for the hybrid semantic layer.

Run from the repository root with a configured provider credential:

    SEMANTIC_MODEL=deepseek-chat python3 docs/reviews/semantic-hybrid-evidence/live-llm-semantic.py

It calls the real ``OpenAICompatibleProvider`` through the normal
``LLMSemanticExtractor`` -> deterministic ``SemanticValidator`` path and prints one
bounded JSON record per case. It records the typed candidate, canonical constraints,
provenance, validation result and any clarification/failure reason. It never records
raw prompt text, hidden reasoning or credentials.

The report validates canonical semantics, not exact model wording. The full compound
query is repeated so the accepted outputs can be checked for one consistent meaning.
"""

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.llm.openai_provider import OpenAICompatibleProvider  # noqa: E402
from app.models.contracts import (CountConstraint, LocationConstraint,  # noqa: E402
                                  NumericConstraint, PitchTypeConstraint,
                                  PopulationConstraint, QualificationConstraint,
                                  RankingConstraint)
from app.semantic.hybrid_parser import HybridSemanticParser  # noqa: E402
from app.semantic.semantic_extractor import (LLMSemanticExtractor,  # noqa: E402
                                             SemanticExtractionError, SemanticVocabulary)
from app.semantic.semantic_validator import (SemanticValidationError,  # noqa: E402
                                             validate_candidate)

COMPOUND = (
    "During the 2025 regular season, on fastballs at least 95 mph in 0-2 or 1-1 counts "
    "near the batter-relative upper edge, rank hitters by maximum exit velocity, "
    "requiring at least 20 batted balls.")

CASES = (
    ("q1_fastball_100bbe",
     "On fastballs at least 95 mph, rank hitters by maximum exit velocity with at "
     "least 100 BBE."),
    ("q2_average_20bbe",
     "Rank hitters by average exit velocity with at least 20 BBE."),
    ("q3_exhibition",
     "In exhibition games, rank hitters by maximum exit velocity."),
    ("q4_exact_counts",
     "In 0-2 or 1-1 counts, rank hitters by maximum exit velocity."),
    ("q5_compound",
     COMPOUND),
    ("ambiguous_high_fastballs",
     "Show me hitters against high fastballs."),
)

STABILITY_REPEATS = 3


def _canonical(result) -> dict:
    summary: dict = {"numeric": [], "summary": sorted(result.summary)}
    for constraint in result.constraints:
        if isinstance(constraint, NumericConstraint):
            summary["numeric"].append([constraint.key, constraint.operator, constraint.value])
        elif isinstance(constraint, PitchTypeConstraint):
            summary["pitch_type"] = constraint.family
        elif isinstance(constraint, CountConstraint):
            summary["count_states"] = [list(state) for state in constraint.exact_states]
        elif isinstance(constraint, RankingConstraint):
            summary["ranking"] = [constraint.metric_key, constraint.aggregation,
                                  constraint.direction, constraint.limit]
        elif isinstance(constraint, QualificationConstraint):
            summary["qualification"] = constraint.min_batted_balls
        elif isinstance(constraint, LocationConstraint):
            summary["location"] = constraint.definition
        elif isinstance(constraint, PopulationConstraint):
            summary["population"] = [list(constraint.game_types), constraint.event_population,
                                     constraint.origin]
    summary["numeric"].sort()
    return summary


def _provenance(result) -> list[dict]:
    return [
        {"kind": item.kind, "key": item.key, "evidence": item.evidence_text,
         "start": item.evidence_start, "end": item.evidence_end, "origin": item.origin}
        for item in result.provenance
    ]


def _extract(extractor: LLMSemanticExtractor, query: str) -> tuple[dict | None, str, str]:
    """Return (typed candidate, status, reason). Bounded, never raw model text."""
    try:
        candidate = extractor.extract(query, SemanticVocabulary())
    except SemanticExtractionError as error:
        return None, "extractor_error", type(error).__name__
    except Exception as error:  # noqa: BLE001 - record unexpected provider failures
        return None, "extractor_error", type(error).__name__
    typed = candidate.model_dump()
    try:
        result = validate_candidate(candidate, query, SemanticVocabulary())
    except SemanticValidationError as error:
        return {"candidate": typed, "status": "rejected",
                "reason": f"{error.code}:{error.detail}",
                "recoverable": error.recoverable}, "rejected", error.code
    return {"candidate": typed, "status": "validated", "canonical": _canonical(result),
            "provenance": _provenance(result),
            "ambiguities": [item.kind for item in result.ambiguities]}, "validated", ""


def main() -> int:
    provider = OpenAICompatibleProvider(settings)
    model = settings.llm_semantic_model
    timeout = settings.llm_request_timeout_seconds
    extractor = LLMSemanticExtractor(provider, model, timeout)
    parser = HybridSemanticParser(extractor=extractor)
    provider_id = {"provider": "OpenAICompatibleProvider",
                   "base_url": settings.deepseek_base_url, "model": model,
                   "timeout_seconds": timeout}

    for case_id, query in CASES:
        record, status, reason = _extract(extractor, query)
        hybrid = parser.parse(query)
        print(json.dumps({
            "case": case_id, "query": query, "provider": provider_id,
            "llm_status": status, "llm_reason": reason,
            "llm": record,
            "hybrid": {"extractor": hybrid.extractor, "fallback": hybrid.fallback_reason,
                       "clarification": hybrid.clarification_reason,
                       "location_wording_requested": hybrid.location_wording_requested,
                       "canonical": _canonical(hybrid),
                       "ambiguities": [item.kind for item in hybrid.ambiguities]},
        }, ensure_ascii=False, default=str))

    # The normal clarification lifecycle must be reached for ambiguous location
    # wording; the LLM must not silently pick a location definition.
    from app.runtime import build_pipeline  # noqa: PLC0415
    with tempfile.TemporaryDirectory() as directory:
        pipeline = build_pipeline(runtime_dir=Path(directory), llm_provider=provider)
        try:
            waiting = pipeline.analyze("Show me hitters against high fastballs.",
                                       run_id="live-llm-ambiguous")
            print(json.dumps({
                "case": "ambiguous_clarification_lifecycle",
                "provider": provider_id,
                "needs_clarification": waiting.needs_clarification,
                "kinds": [item.kind for item in waiting.clarifications],
                "reasons": [item.reason for item in waiting.clarifications],
                "options": [option.value for item in waiting.clarifications
                            for option in item.options],
            }, ensure_ascii=False, default=str))
        finally:
            pipeline.close()

    # Repeated-run stability: every accepted compound output must normalize to one
    # canonical meaning (byte-identical JSON is not required).
    accepted = []
    for attempt in range(STABILITY_REPEATS):
        record, status, reason = _extract(extractor, COMPOUND)
        hybrid = parser.parse(COMPOUND)
        canonical = _canonical(hybrid)
        accepted.append(canonical)
        print(json.dumps({
            "case": "q5_stability", "attempt": attempt + 1, "provider": provider_id,
            "llm_status": status, "llm_reason": reason,
            "hybrid_extractor": hybrid.extractor, "hybrid_fallback": hybrid.fallback_reason,
            "canonical": canonical,
        }, ensure_ascii=False, default=str))
    stable = all(item == accepted[0] for item in accepted)
    print(json.dumps({"case": "q5_stability_summary", "repeats": STABILITY_REPEATS,
                      "accepted": sum(1 for item in accepted if item is not None),
                      "consistent": stable}, ensure_ascii=False))
    return 0 if stable else 1


if __name__ == "__main__":
    raise SystemExit(main())
