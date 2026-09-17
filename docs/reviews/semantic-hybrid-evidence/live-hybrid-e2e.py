"""Real E2E evidence for the hybrid semantic parser.

Run from the repository root:

    python3 docs/reviews/semantic-hybrid-evidence/live-hybrid-e2e.py

It resolves the location clarification, runs the real read-only runtime, then captures
the generated PostgreSQL/Parquet SQL through a recording wrapper around the real
executor. No synthetic adapter or fallback is used for the SQL capture.
"""

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.models.clarification import ClarificationAnswer  # noqa: E402
from app.models.contracts import (CountConstraint, LocationConstraint,  # noqa: E402
                                  NumericConstraint, PopulationConstraint,
                                  QualificationConstraint, RankingConstraint)
from app.models.planning import AgentTask  # noqa: E402
from app.pipeline import AnalysisPipeline  # noqa: E402
from app.semantic.field_mapping import BATTER_RELATIVE_UPPER_EDGE, FieldMappingRegistry  # noqa: E402
from app.semantic.requirement_decomposer import RuleBasedRequirementDecomposer  # noqa: E402
from app.tools.execution import DuckDBReadOnlyExecutor, PostgresReadOnlyExecutor  # noqa: E402
from app.tools.statcast import ParquetStatcastTool, PostgresStatcastTool  # noqa: E402

COMPOUND = (
    "During the {year} regular season, on fastballs at least 95 mph in 0-2 or 1-1 counts "
    "near the batter-relative upper edge, rank hitters by maximum exit velocity, "
    "requiring at least 20 batted balls.")


class RecordingExecutor:
    def __init__(self, inner):
        self._inner = inner
        self.sql = []
        self.results = []

    def execute_with_rows(self, sql):
        self.sql.append(sql)
        result = self._inner.execute_with_rows(sql)
        self.results.append(result[0])
        return result


def _semantic_summary(objective):
    summary = {}
    for constraint in objective.constraints:
        if isinstance(constraint, NumericConstraint):
            summary.setdefault("numeric", []).append(
                [constraint.key, constraint.operator, constraint.value])
        elif isinstance(constraint, CountConstraint):
            summary["count_states"] = [list(state) for state in constraint.exact_states]
        elif isinstance(constraint, RankingConstraint):
            summary["ranking"] = [constraint.metric_key, constraint.aggregation,
                                  constraint.direction, constraint.limit]
        elif isinstance(constraint, QualificationConstraint):
            summary["qualification_min_batted_balls"] = constraint.min_batted_balls
        elif isinstance(constraint, LocationConstraint):
            summary["location"] = constraint.definition
        elif isinstance(constraint, PopulationConstraint):
            summary["population"] = [list(constraint.game_types), constraint.event_population]
        elif constraint.key == "date_range":
            summary["period"] = list(constraint.values)
    return summary


def _capture_sql(requirement, source_kind, all_requirements):
    if source_kind == "PARQUET":
        inner = DuckDBReadOnlyExecutor(settings.parquet_archive_path)
        tool = ParquetStatcastTool(
            [requirement], FieldMappingRegistry(), inner,
            archive_glob=str(settings.parquet_archive_path / "mlb_statcast_*.parquet"))
    else:
        inner = PostgresReadOnlyExecutor(settings, allowed_tables=("statcast_pitches",
                                                                   "player_dictionary"))
        tool = PostgresStatcastTool([requirement], FieldMappingRegistry(), inner)
    recorder = RecordingExecutor(tool._executor)
    tool._executor = recorder
    task = AgentTask(task_id="live-hybrid", objective_ref=requirement.objective_ref,
                     requirement_refs=(requirement.requirement_id,),
                     description=requirement.description)
    result = tool.execute(task)
    raw_status = recorder.results[0].status if recorder.results else ""
    return result, recorder.sql, raw_status


def main() -> int:
    output = []
    with tempfile.TemporaryDirectory() as directory:
        pipeline = AnalysisPipeline.default(runtime_dir=Path(directory))
        try:
            for year, run_id in ((2025, "hybrid-2025"), (2023, "hybrid-2023")):
                query = COMPOUND.format(year=year)
                waiting = pipeline.analyze(query, run_id=run_id)
                trace = list(waiting.semantic_trace)
                clarification = waiting.clarifications[0] if waiting.clarifications else None
                option = next((item for item in (clarification.options if clarification else ())
                               if item.value == BATTER_RELATIVE_UPPER_EDGE), None)
                if clarification is None or option is None:
                    output.append({"year": year, "query": query, "error": "no location clarification"})
                    continue
                result = pipeline.resume_clarification(run_id, ClarificationAnswer(
                    clarification_ref=clarification.clarification_id,
                    chosen_option_id=option.option_id))
                objective = result.objectives[0]
                requirement = RuleBasedRequirementDecomposer().decompose(objective)[0]
                source_kind = (result.response_packages[0].accepted_evidence[0].source_kind
                               if result.response_packages and result.response_packages[0].accepted_evidence
                               else ("POSTGRES" if year >= 2024 else "PARQUET"))
                tool_result, sql, raw_status = _capture_sql(requirement, source_kind, (requirement,))
                output.append({
                    "year": year, "query": query,
                    "clarification": clarification.question,
                    "clarification_reason": clarification.reason,
                    "semantic": _semantic_summary(objective),
                    "semantic_trace": trace,
                    "objective_status": list(result.objective_statuses),
                    "source_kind": source_kind,
                    "tool_status": tool_result.status,
                    "tool_error_code": tool_result.error_code or "",
                    "raw_executor_status": raw_status,
                    "accepted_rows": (tool_result.artifact.row_count
                                      if tool_result.artifact else 0),
                    "sql": sql[:1],
                })

            cross_query = (
                "During the regular season in 2023 vs 2024, on fastballs at least 95 mph "
                "in 0-2 or 1-1 counts near the batter-relative upper edge, rank hitters by "
                "maximum exit velocity, requiring at least 20 batted balls.")
            waiting = pipeline.analyze(cross_query, run_id="hybrid-cross")
            clarification = waiting.clarifications[0] if waiting.clarifications else None
            option = next((item for item in (clarification.options if clarification else ())
                           if item.value == BATTER_RELATIVE_UPPER_EDGE), None)
            if clarification is not None and option is not None:
                result = pipeline.resume_clarification("hybrid-cross", ClarificationAnswer(
                    clarification_ref=clarification.clarification_id,
                    chosen_option_id=option.option_id))
                entries = []
                for objective in result.objectives:
                    requirement = RuleBasedRequirementDecomposer().decompose(objective)[0]
                    window = next((list(c.values) for c in objective.constraints
                                   if c.key == "date_range"), [])
                    source_kind = "POSTGRES" if window and int(window[0][:4]) >= 2024 else "PARQUET"
                    tool_result, sql, raw_status = _capture_sql(requirement, source_kind,
                                                                (requirement,))
                    entries.append({
                        "period": window, "source_kind": source_kind,
                        "semantic": _semantic_summary(objective),
                        "raw_executor_status": raw_status,
                        "sql": sql[:1],
                    })
                output.append({"cross_source": True, "query": cross_query,
                               "statuses": list(result.objective_statuses),
                               "objectives": entries})
        finally:
            pipeline.close()
    for item in output:
        print(json.dumps(item, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
