"""Three bounded provider calls, validating the exact candidate returned each time."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
from app.config import settings
from app.llm.openai_provider import OpenAICompatibleProvider
from app.semantic.semantic_extractor import LLMSemanticExtractor
from app.semantic.hybrid_parser import HybridSemanticParser

class Recorded:
    name = "llm"
    candidate = None
    def extract(self, query, vocabulary):
        self.candidate = LLMSemanticExtractor(OpenAICompatibleProvider(settings),
                                              settings.llm_semantic_model, 20).extract(query, vocabulary)
        return self.candidate

compound = ("During the 2025 regular season, on fastballs at least 95 mph in 0-2 or 1-1 counts "
            "near the batter-relative upper edge, rank hitters by maximum exit velocity, "
            "requiring at least 20 batted balls.")
for case, query in [("compound",compound),("explicit_population", "Rank hitters by maximum exit velocity over all pitches in 2025"),
                    ("word_qualification", "Rank hitters by maximum exit velocity with at least twenty batted balls in 2025")]:
    extractor = Recorded()
    result = HybridSemanticParser(extractor=extractor).parse(query)
    print(json.dumps({"case":case,"query":query,"model":settings.llm_semantic_model,
                      "candidate":extractor.candidate.model_dump(mode="json") if extractor.candidate else None,
                      "extractor":result.extractor,"fallback":result.fallback_reason,
                      "clarification":result.clarification_reason,
                      "constraints":[c.model_dump(mode="json") for c in result.constraints]}),flush=True)
