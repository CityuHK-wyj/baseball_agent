"""Independent containment gate. Exit 1 means release blockers remain; no live I/O."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.models.semantic_candidate import SemanticCandidate
from app.semantic.semantic_extractor import SemanticVocabulary, SemanticProviderError
from app.semantic.semantic_validator import validate_candidate, SemanticValidationError
from app.semantic.hybrid_parser import HybridSemanticParser
from pydantic import ValidationError
from app.models.contracts import AnalysisObjective
from app.semantic.requirement_decomposer import RuleBasedRequirementDecomposer
from app.models.planning import AgentTask
from app.semantic.field_mapping import FieldMappingRegistry
from app.tools.statcast import PostgresStatcastTool
from app.tools.results import ToolResult


def proposed(query, **fields):
    return SemanticCandidate.model_validate({"constraints": [{
        "origin": "USER_EXPLICIT", "evidence": {"text": query}, **fields}]})


def main():
    failures = []
    cases = [
        ("numeric_value_operator", "fastballs at least 95 mph",
         dict(kind="NUMERIC", metric="pitch_velocity", operator="LTE", value=20)),
        ("qualification_value", "at least 100 BBE",
         dict(kind="QUALIFICATION", min_batted_balls=3)),
        ("ranking_identity", "top 5 by maximum exit velocity",
         dict(kind="RANKING", metric_key="pitch_velocity", aggregation="AVG", limit=50)),
        ("game_type", "exhibition games",
         dict(kind="POPULATION", game_types=["REGULAR_SEASON"])),
        ("exact_count", "0-2 or 1-1 counts",
         dict(kind="COUNT", states=[dict(balls=3, strikes=1)])),
        ("qualification_owned_number", "at least 20 BBE",
         dict(kind="NUMERIC", metric="pitch_velocity", operator="GTE", value=20)),
        ("physical_field", "release_speed >= 95",
         dict(kind="NUMERIC", metric="release_speed", operator="GTE", value=95)),
        ("invalid_count", "5-4 counts", dict(kind="COUNT", states=[dict(balls=5,strikes=4)])),
        ("invalid_aggregation", "maximum exit velocity",
         dict(kind="RANKING", metric_key="exit_velocity", aggregation="SUPER_MAX")),
    ]
    for name, query, fields in cases:
        try:
            result = validate_candidate(proposed(query, **fields), query, SemanticVocabulary())
            failures.append(name)
            record = {"accepted": [c.model_dump(mode="json") for c in result.constraints]}
        except (ValidationError, SemanticValidationError) as error:
            record = {"rejected": type(error).__name__, "code": getattr(error,"code","SCHEMA")}
        print(json.dumps({"case":name,"query":query,**record}))

    class Failure:
        name = "unavailable"
        def extract(self, *args):
            raise SemanticProviderError("review simulated provider failure")

    parser = HybridSemanticParser(extractor=Failure())
    for name, query in [
        ("explicit_all_pitches", "Rank hitters by maximum exit velocity over all pitches in 2025"),
        ("conflicting_limits", "top 5 or top 10 by maximum exit velocity in 2025"),
        ("fallback_word_qualification", "top 5 by maximum exit velocity with at least twenty batted balls in 2025"),
    ]:
        result = parser.parse(query)
        safe = result.failed_closed or result.location_wording_requested
        if not safe:
            failures.append(name)
        print(json.dumps({"case":name,"query":query,"clarification":result.clarification_reason,
                          "constraints":[c.model_dump(mode="json") for c in result.constraints]}))

    # A schema-valid model can omit an explicit population, bypassing the conflict
    # check altogether. The validator must reconcile proposals with the query.
    query = "top 5 by maximum exit velocity over all pitches in 2025"
    candidate = proposed("top 5 by maximum exit velocity", kind="RANKING",
                         metric_key="exit_velocity", aggregation="MAX", limit=5)
    try:
        result = validate_candidate(candidate, query, SemanticVocabulary())
        failures.append("omitted_explicit_population")
        record = {"constraints":[c.model_dump(mode="json") for c in result.constraints]}
    except (ValidationError, SemanticValidationError) as error:
        record = {"rejected":type(error).__name__}
    print(json.dumps({"case":"omitted_explicit_population", "query":query, **record}))

    # Trace an accepted wrong candidate through requirement, descriptor and SQL.
    query = "top 5 by maximum exit velocity on fastballs at least 95 mph with at least 100 BBE in 2025"
    candidate = SemanticCandidate(constraints=(
        proposed("top 5 by maximum exit velocity", kind="RANKING", metric_key="exit_velocity",
                 aggregation="AVG", limit=5).constraints[0],
        proposed("fastballs at least 95 mph", kind="NUMERIC", metric="pitch_velocity",
                 operator="LTE", value=20).constraints[0],
        proposed("at least 100 BBE", kind="QUALIFICATION", min_batted_balls=3).constraints[0]))
    try:
        result = validate_candidate(candidate, query, SemanticVocabulary())
        objective = AnalysisObjective(objective_id="review", raw_query=query, description=query,
                                      constraints=result.constraints)
        requirement = RuleBasedRequirementDecomposer().decompose(objective)[0]
        class Capture:
            sql = []
            def execute_with_rows(self, sql):
                self.sql.append(sql)
                return ToolResult.ok(0), []
        capture = Capture()
        tool = PostgresStatcastTool([requirement], FieldMappingRegistry(), capture)
        tool.execute(AgentTask(task_id="review", objective_ref="review", description=query,
                              requirement_refs=(requirement.requirement_id,)))
        print(json.dumps({"case":"wrong_candidate_reaches_sql", "query":query,
                          "candidate":candidate.model_dump(mode="json"),
                          "requirement":requirement.model_dump(mode="json"),"sql":capture.sql}))
    except (ValidationError, SemanticValidationError) as error:
        print(json.dumps({"case":"wrong_candidate_reaches_sql", "rejected":type(error).__name__}))

    query = "Show me hitters against high fastballs."
    candidate = SemanticCandidate.model_validate({"constraints":[
        {"kind":"LOCATION", "definition":"BATTER_RELATIVE_UPPER_EDGE",
         "evidence":{"text":"high"}}], "ambiguities":[
             {"kind":"location.upper_edge","evidence":{"text":"high fastballs"}}]})
    try:
        result = validate_candidate(candidate, query, SemanticVocabulary())
        if not result.failed_closed and not result.location_wording_requested:
            failures.append("location_ambiguity_suppressed")
        print(json.dumps({"case":"location_ambiguity_suppressed","query":query,
                          "needs_clarification":result.failed_closed or result.location_wording_requested,
                          "constraints":[c.model_dump(mode="json") for c in result.constraints]}))
    except (ValidationError, SemanticValidationError) as error:
        print(json.dumps({"case":"location_ambiguity_suppressed", "rejected":type(error).__name__}))
    print(json.dumps({"unresolved_blockers": failures}))
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
