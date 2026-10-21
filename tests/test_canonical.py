from app.capabilities.analytics.canonical import build_canonical_query, is_general_performance
from app.capabilities.analytics.compiler import SQLCompiler
from app.capabilities.analytics.guard import guard_sql
from app.capabilities.analytics.ir import validate_query


def _intent(role="BATTER", objective="overall batting performance", season=2025, population="REGULAR_SEASON"):
    return {
        "objective": objective,
        "measure": None,
        "population": population,
        "season": season,
        "entities": [{"role": role, "ids": [660271], "names": ["Shohei Ohtani"]}],
    }


def test_general_batting_is_canonical():
    assert is_general_performance(_intent())


def test_specific_split_is_not_canonical():
    assert not is_general_performance(_intent(objective="exit velocity vs left-handed pitching"))
    assert not is_general_performance(_intent(objective="面对左投的击球初速分项"))


def test_canonical_query_compiles_and_passes_guard():
    query = validate_query(build_canonical_query(_intent()))
    compiled = SQLCompiler(parquet_archive="/tmp").compile(query, "POSTGRES")
    assert guard_sql(compiled.sql).ok
    assert '"batter_id" IN (660271)' in compiled.sql
    assert "AVG(" not in compiled.sql  # rate metrics are derived, not raw AVG of counts
    assert "COUNT" in compiled.sql


def test_canonical_pitcher_uses_pitcher_filter_and_velocity():
    query = validate_query(build_canonical_query(_intent(role="PITCHER", objective="overall pitching performance")))
    compiled = SQLCompiler(parquet_archive="/tmp").compile(query, "POSTGRES")
    assert '"pitcher_id" IN (660271)' in compiled.sql
    assert '"release_speed"' in compiled.sql
    assert guard_sql(compiled.sql).ok


def test_canonical_without_entities_returns_none():
    intent = _intent()
    intent["entities"] = []
    assert build_canonical_query(intent) is None
