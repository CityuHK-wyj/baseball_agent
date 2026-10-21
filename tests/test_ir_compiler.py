import pytest

from app.capabilities.analytics.compiler import SQLCompiler, render_literal
from app.capabilities.analytics.guard import guard_sql
from app.capabilities.analytics.ir import (
    Aggregate,
    AnalyticalQuery,
    Condition,
    Derived,
    EntityFilter,
    IRError,
    Operand,
    Qualification,
    validate_query,
)


def batting_query() -> AnalyticalQuery:
    return AnalyticalQuery(
        relation="pitch",
        entity_filters=[EntityFilter(role="BATTER", ids=[660271], names=["Shohei Ohtani"])],
        population="REGULAR_SEASON",
        season=2025,
        aggregates=[
            Aggregate(name="pa", function="COUNT_NON_NULL", field="events"),
            Aggregate(
                name="hits",
                function="COUNT_IF",
                condition=Condition(op="IN", field="events", values=["single", "double", "triple", "home_run"]),
            ),
            Aggregate(
                name="at_bats",
                function="COUNT_IF",
                condition=Condition(
                    op="AND",
                    conditions=[
                        Condition(op="IS_NOT_NULL", field="events"),
                        Condition(op="NOT", conditions=[Condition(op="IN", field="events", values=["walk"])]),
                    ],
                ),
            ),
        ],
        derived=[
            Derived(
                name="avg",
                op="SAFE_DIV",
                left=Operand(kind="aggregate", name="hits"),
                right=Operand(kind="aggregate", name="at_bats"),
            )
        ],
    )


def test_validate_and_compile_postgres():
    query = validate_query(batting_query())
    compiled = SQLCompiler(parquet_archive="/tmp/x").compile(query, "POSTGRES")
    assert '"batter_id" IN (660271)' in compiled.sql
    assert "EXTRACT(YEAR FROM game_date) = 2025" in compiled.sql
    assert '"game_type" IN (\'R\')' in compiled.sql
    assert "batter_id" in compiled.applied_fields
    assert guard_sql(compiled.sql).ok


def test_parquet_requires_read_parquet_and_renamed_keys():
    query = validate_query(batting_query())
    compiled = SQLCompiler(parquet_archive="/data/archive").compile(query, "PARQUET")
    assert "read_parquet('/data/archive/mlb_statcast_*.parquet')" in compiled.sql
    assert '"batter" IN (660271)' in compiled.sql


def test_unknown_field_rejected():
    query = batting_query()
    query.aggregates[0].field = "not_a_field"
    with pytest.raises(IRError) as err:
        validate_query(query)
    assert err.value.code == "UNKNOWN_FIELD"


def test_derived_cycle_rejected():
    query = AnalyticalQuery(
        aggregates=[Aggregate(name="n", function="COUNT", field="*")],
        derived=[
            Derived(name="a", op="ADD", left=Operand(kind="derived", name="b"), right=Operand(kind="literal", value=1)),
            Derived(name="b", op="ADD", left=Operand(kind="derived", name="a"), right=Operand(kind="literal", value=1)),
        ],
    )
    with pytest.raises(IRError):
        validate_query(query)


def test_derived_can_forward_reference_order_independent():
    query = AnalyticalQuery(
        group_by=["season"],
        aggregates=[Aggregate(name="hits", function="COUNT_NON_NULL", field="events")],
        derived=[
            Derived(name="outer", op="MUL", left=Operand(kind="derived", name="inner"), right=Operand(kind="literal", value=2)),
            Derived(name="inner", op="ADD", left=Operand(kind="aggregate", name="hits"), right=Operand(kind="literal", value=1)),
        ],
    )
    validate_query(query)
    compiled = SQLCompiler().compile(query, "POSTGRES")
    assert '"outer"' not in compiled.sql  # aliases are sanitized, expressions inlined
    assert compiled.output_columns == ["season", "hits", "outer", "inner"]


def test_qualification_having():
    query = batting_query()
    query.qualification = Qualification(basis="PA", minimum=25)
    compiled = SQLCompiler().compile(validate_query(query), "POSTGRES")
    assert "HAVING COUNT(\"events\") >= 25" in compiled.sql


def test_limit_bounds():
    query = batting_query()
    query.limit = 10000
    with pytest.raises(IRError):
        validate_query(query)


def test_literal_rendering_escapes_quotes():
    assert render_literal("O'Hara") == "'O''Hara'"
    assert render_literal(3) == "3"
