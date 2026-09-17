"""Live dual-semantic E2E evidence.

Runs the real DeepSeek extractor + reviewer through the dual parser, then the real
read-only PostgreSQL / Parquet adapters, recording the canonical semantics, the
reconciliation outcome and the exact SQL executed. Never prints credentials.

Run from the repository root (requires credentials and running sources):

    python3 docs/reviews/dual-semantic-live-evidence.py
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config import settings  # noqa: E402
from app.llm.openai_provider import OpenAICompatibleProvider  # noqa: E402
from app.models.clarification import ClarificationAnswer  # noqa: E402
from app.runtime import build_pipeline  # noqa: E402
from app.tools.execution import DuckDBReadOnlyExecutor, PostgresReadOnlyExecutor  # noqa: E402

COMPOUND = ("During the {period} regular season, on fastballs at least 95 mph in 0-2 or 1-1 "
            "counts near the batter-relative upper edge, rank hitters by maximum exit velocity, "
            "requiring at least 20 batted balls.")


def _capture(record):
    def wrap(method, source):
        def run(self, statement):
            result, rows = method(self, statement)
            record.append({"source": source, "sql": statement, "status": result.status,
                           "row_count": len(rows)})
            return result, rows
        return run
    return wrap


def run_case(period: str) -> dict:
    query = COMPOUND.format(period=period)
    last: dict | None = None
    for attempt in range(3):
        last = _run_case_once(period, query, attempt)
        if last.get("statuses"):
            return last
    return last


def _run_case_once(period: str, query: str, attempt: int) -> dict:
    sql: list[dict] = []
    with tempfile.TemporaryDirectory() as directory:
        run_id = f"dual-live-{period.replace(' ', '-')}-{attempt}"
        with patch.object(DuckDBReadOnlyExecutor, "execute_with_rows",
                          _capture(sql)(DuckDBReadOnlyExecutor.execute_with_rows, "PARQUET")), \
                patch.object(PostgresReadOnlyExecutor, "execute_with_rows",
                             _capture(sql)(PostgresReadOnlyExecutor.execute_with_rows, "POSTGRES")):
            pipeline = build_pipeline(runtime_dir=Path(directory),
                                      llm_provider=OpenAICompatibleProvider(settings))
            try:
                result = pipeline.analyze(query, run_id=run_id)
                if result.needs_clarification:
                    request = result.clarifications[0]
                    option = next((item for item in request.options
                                   if item.value == "BATTER_RELATIVE_UPPER_EDGE"), None)
                    if option is not None:
                        result = pipeline.resume_clarification(
                            run_id, ClarificationAnswer(
                                clarification_ref=request.clarification_id,
                                chosen_option_id=option.option_id))
                return {
                    "period": period,
                    "attempt": attempt,
                    "query": query,
                    "trace": list(result.semantic_trace),
                    "objectives": [objective.model_dump(mode="json")
                                   for objective in result.objectives],
                    "statuses": list(result.objective_statuses),
                    "responses": list(result.responses),
                    "sql": sql,
                }
            finally:
                pipeline.close()


def run_knowledge() -> dict:
    with tempfile.TemporaryDirectory() as directory:
        pipeline = build_pipeline(runtime_dir=Path(directory))
        try:
            result = pipeline.analyze("DFA是什么意思？")
            return {"query": "DFA是什么意思？", "statuses": list(result.objective_statuses),
                    "trace": list(result.semantic_trace)}
        finally:
            pipeline.close()


def main() -> int:
    report = {"models": {"extractor": settings.semantic_extractor_model,
                         "reviewer": settings.semantic_reviewer_model}}
    print(json.dumps(report), flush=True)
    print(json.dumps(run_knowledge(), ensure_ascii=False), flush=True)
    for period in ("2025", "2023", "2023 vs 2024"):
        try:
            print(json.dumps(run_case(period), ensure_ascii=False), flush=True)
        except Exception as error:  # noqa: BLE001 - evidence must be recorded, not crash
            print(json.dumps({"period": period, "error": type(error).__name__,
                              "detail": str(error)[:300]}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
