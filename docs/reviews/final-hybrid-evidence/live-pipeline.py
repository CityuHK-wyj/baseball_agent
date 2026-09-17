"""Real model -> validator -> normalizer -> router -> real read-only source probes."""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path.cwd()))
from app.config import settings
from app.llm.openai_provider import OpenAICompatibleProvider
from app.runtime import build_pipeline
from app.models.clarification import ClarificationAnswer
from app.tools.execution import DuckDBReadOnlyExecutor, PostgresReadOnlyExecutor

for period in ("2025", "2023", "2023 vs 2024"):
    query = (f"During the {period} regular season, on fastballs at least 95 mph in 0-2 or 1-1 counts "
             "near the batter-relative upper edge, rank hitters by maximum exit velocity, "
             "requiring at least 20 batted balls.")
    sql = []
    def wrap(method, source):
        def run(self, statement):
            result, rows = method(self, statement)
            sql.append({"source":source,"sql":statement,"status":result.status,"row_count":len(rows)})
            return result, rows
        return run
    with tempfile.TemporaryDirectory() as directory:
        with patch.object(DuckDBReadOnlyExecutor,"execute_with_rows",wrap(DuckDBReadOnlyExecutor.execute_with_rows,"PARQUET")), patch.object(PostgresReadOnlyExecutor,"execute_with_rows",wrap(PostgresReadOnlyExecutor.execute_with_rows,"POSTGRES")):
            pipeline = build_pipeline(runtime_dir=Path(directory), llm_provider=OpenAICompatibleProvider(settings))
            result = pipeline.analyze(query,run_id="live-pipeline")
            trace = result.semantic_trace
            if result.needs_clarification:
                request = result.clarifications[0]
                option = next((o for o in request.options if o.value=="BATTER_RELATIVE_UPPER_EDGE"),None)
                if option:
                    result = pipeline.resume_clarification("live-pipeline",ClarificationAnswer(
                        clarification_ref=request.clarification_id,chosen_option_id=option.option_id))
            print(json.dumps({"period":period,"query":query,"trace":trace,
                              "objectives":[o.model_dump(mode="json") for o in result.objectives],
                              "statuses":result.objective_statuses,"clarifications":[c.model_dump(mode="json") for c in result.clarifications],
                              "packages":[p.model_dump(mode="json") for p in result.response_packages],"sql":sql}),flush=True)
            pipeline.close()
