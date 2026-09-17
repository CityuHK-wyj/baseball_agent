"""Focused gate: hybrid semantic extraction and compositional semantics.

Run from the repository root:

    python3 docs/reviews/v01-semantic-hybrid-gate.py

It exits nonzero while any of the following remain wrong:

* the 20-case semantic evaluation corpus (typed semantics or clarification);
* the five compositional P1 failures reported by the independent Codex review,
  checked end-to-end through the canonical requirement and the generated PostgreSQL /
  Parquet SQL.

The gate tests semantics, not model wording, and runs without live LLM credentials by
exercising the deterministic extractor and the deterministic validator. The LLM seam is
covered separately by tests/semantic/test_semantic_layer.py with a mocked provider.
"""

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.models.contracts import (AnalysisObjective, CountConstraint,  # noqa: E402
                                  NumericConstraint, PopulationConstraint,
                                  QualificationConstraint, RankingConstraint)
from app.models.planning import AgentTask  # noqa: E402
from app.models.semantic_candidate import SemanticCandidate  # noqa: E402
from app.semantic.field_mapping import (BATTER_RELATIVE_UPPER_EDGE,  # noqa: E402
                                        FieldMappingRegistry, ZONE_UPPER_OUTSIDE,
                                        ZONE_UPPER_THIRD)
from app.semantic.hybrid_parser import HybridSemanticParser  # noqa: E402
from app.semantic.entity_resolver import EntityDictionary, EntityResolver  # noqa: E402
from app.semantic.normalizer import SemanticNormalizer  # noqa: E402
from app.semantic.objective_extractor import RuleBasedObjectiveExtractor  # noqa: E402
from app.semantic.requirement_decomposer import RuleBasedRequirementDecomposer  # noqa: E402
from app.semantic.semantic_extractor import SemanticVocabulary  # noqa: E402
from app.semantic.semantic_validator import (SemanticValidationError,  # noqa: E402
                                             validate_candidate)
from app.tools.results import ToolResult  # noqa: E402
from app.tools.statcast import ParquetStatcastTool, PostgresStatcastTool  # noqa: E402

from pydantic import ValidationError  # noqa: E402

CORPUS = json.loads((Path(__file__).parent / "semantic-eval-corpus.json").read_text())
VOCABULARY = SemanticVocabulary()
PARSER = HybridSemanticParser()


class Capture:
    def __init__(self):
        self.sql = []

    def execute_with_rows(self, sql):
        self.sql.append(sql)
        rows = [(date(2023, 3, 15), date(2023, 11, 1))] if "MIN(" in sql else (
            [] if "player_dictionary" in sql else [(1, 100, 90.0, 100.0)])
        return ToolResult.ok(len(rows)), rows


def _numeric_set(constraints):
    return sorted([[item.key, item.operator, float(item.value)]
                   for item in constraints if isinstance(item, NumericConstraint)])


def _evaluate_parse(case):
    expect = case["expect"]
    result = PARSER.parse(case["query"])
    observed = {"constraints": [item.kind for item in result.constraints],
                "clarification": result.location_wording_requested or result.failed_closed}
    passed = True
    ranking = next((item for item in result.constraints if isinstance(item, RankingConstraint)),
                   None)
    if expect.get("ranking") is not None:
        passed = passed and ranking is not None and \
            [ranking.metric_key, ranking.aggregation] == expect["ranking"]
    if "numeric" in expect:
        passed = passed and _numeric_set(result.constraints) == sorted(expect["numeric"])
    qualification = next((item for item in result.constraints
                          if isinstance(item, QualificationConstraint)), None)
    expected_qualification = expect.get("qualification")
    passed = passed and ((qualification is None and expected_qualification is None) or
                         (qualification is not None and
                          qualification.min_batted_balls == expected_qualification))
    population = next((item for item in result.constraints
                       if isinstance(item, PopulationConstraint)), None)
    if expect.get("population") is not None:
        passed = passed and population is not None and \
            list(population.game_types) == expect["population"]
    count = next((item for item in result.constraints if isinstance(item, CountConstraint)), None)
    if expect.get("count_states") is not None:
        expected_states = tuple(tuple(pair) for pair in expect["count_states"])
        passed = passed and count is not None and count.exact_states == expected_states
    elif "count_states" in expect:
        passed = passed and count is None
    if "clarification" in expect:
        passed = passed and observed["clarification"] == expect["clarification"]
    return case["id"], passed, observed


def _evaluate_candidate(case):
    observed = "NO_ERROR"
    try:
        candidate = SemanticCandidate.model_validate(case["candidate"])
        validate_candidate(candidate, case["query"], VOCABULARY)
    except SemanticValidationError as error:
        observed = error.code
    except ValidationError:
        observed = "SCHEMA"
    return case["id"], observed == case["expect_error"], {"error": observed}


def _evaluate_normalizer(case):
    normalizer = SemanticNormalizer(
        RuleBasedObjectiveExtractor(id_factory=lambda prefix: f"{prefix}-gate"),
        EntityResolver(EntityDictionary(), id_factory=lambda prefix: f"{prefix}-gate"),
        EntityDictionary(), id_factory=lambda prefix: f"{prefix}-gate",
        semantic_parser=PARSER)
    result = normalizer.normalize(case["query"])
    windows = []
    for objective in result.objectives:
        window = next((item for item in objective.constraints if item.key == "date_range"), None)
        if window is not None:
            windows.append(list(window.values))
    expected = [list(pair) for pair in case["expect_windows"]]
    passed = windows == expected and not result.needs_clarification
    # The location clarification must still offer the exact batter-relative definition.
    if result.clarifications:
        options = {option.value for clarification in result.clarifications
                   for option in clarification.options}
        passed = passed and {BATTER_RELATIVE_UPPER_EDGE, ZONE_UPPER_THIRD,
                             ZONE_UPPER_OUTSIDE} <= options
    return case["id"], passed, {"windows": windows}


def _requirement(query):
    parsed = PARSER.parse(query)
    objective = AnalysisObjective(objective_id="gate", raw_query=query, description=query,
                                  constraints=parsed.constraints)
    return RuleBasedRequirementDecomposer(
        id_factory=lambda prefix: f"{prefix}-gate").decompose(objective)[0]


def _sql(query, tool_class):
    capture = Capture()
    tool = tool_class([_requirement(query)], FieldMappingRegistry(), capture)
    task = AgentTask(task_id="gate", objective_ref="gate",
                     requirement_refs=("requirement-gate",), description=query)
    tool.execute(task)
    return capture.sql[0]


P1_CASES = (
    ("p1_qualification_velocity_collision",
     "top 5 by maximum exit velocity on fastballs at least 95 mph, at least 100 BBE in 2023",
     lambda sql: "release_speed >= 95.0" in sql and "HAVING COUNT(*) >= 100" in sql
     and "release_speed >= 100.0" not in sql),
    ("p1_symbolic_qualification",
     "top 5 by maximum exit velocity with >= 20 BBE in 2023",
     lambda sql: "HAVING COUNT(*) >= 20" in sql and "launch_speed >= 20.0" not in sql
     and "release_speed >= 20.0" not in sql),
    ("p1_ranking_metric_collision",
     "top 5 hitters facing pitch velocity >= 95 mph ranked by maximum exit velocity in 2023",
     lambda sql: "ORDER BY MAX(launch_speed) DESC" in sql
     and "ORDER BY AVG(release_speed)" not in sql),
    ("p1_exhibition",
     "top 5 by maximum exit velocity in exhibition games in 2023",
     lambda sql: "game_type IN ('E', 'A')" in sql and "game_type IN ('R')" not in sql),
    ("p1_mixed_counts",
     "top 5 by maximum exit velocity on 0-2 or 1-1 counts in 2023",
     lambda sql: "(balls = 0 AND strikes = 2)" in sql and "(balls = 1 AND strikes = 1)" in sql
     and "balls IN" not in sql),
)


def main() -> int:
    failures = []
    for case in CORPUS:
        mode = case.get("mode", "parse")
        if mode == "parse":
            identifier, passed, observed = _evaluate_parse(case)
        elif mode == "candidate":
            identifier, passed, observed = _evaluate_candidate(case)
        elif mode == "normalizer":
            identifier, passed, observed = _evaluate_normalizer(case)
        else:
            raise ValueError(f"Unknown corpus mode {mode!r}")
        if not passed:
            failures.append(identifier)
        print(json.dumps({"section": "corpus", "case": identifier, "passed": passed,
                          "observed": observed}), flush=True)

    for identifier, query, predicate in P1_CASES:
        results = {}
        for tool_class in (ParquetStatcastTool, PostgresStatcastTool):
            results[tool_class.source_kind] = _sql(query, tool_class)
        passed = all(predicate(sql) for sql in results.values())
        if not passed:
            failures.append(identifier)
        print(json.dumps({"section": "p1", "case": identifier, "passed": passed,
                          "sql": results}), flush=True)

    print(json.dumps({"unresolved_blockers": failures}))
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
