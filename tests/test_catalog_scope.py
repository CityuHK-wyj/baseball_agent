from datetime import date

from app.capabilities.analytics.catalog import SOURCE_PARQUET, SOURCE_POSTGRES, CATALOG
from app.contracts.models import Scope, ScopeGap
from app.verification.scope import compare_scope


def test_season_routing_postgres():
    source, reason = CATALOG.source_for_window("pitch", season=2025)
    assert source == SOURCE_POSTGRES
    assert reason == ""


def test_season_routing_parquet():
    source, reason = CATALOG.source_for_window("pitch", season=2020)
    assert source == SOURCE_PARQUET


def test_season_outside_coverage():
    source, reason = CATALOG.source_for_window("pitch", season=2010)
    assert source is None
    assert "coverage" in reason


def test_partial_date_window_discloses_gap():
    source, reason = CATALOG.source_for_window(
        "pitch", start=date(2024, 1, 1), end=date(2024, 12, 31)
    )
    assert source == SOURCE_POSTGRES
    assert "partial coverage" in reason


def test_population_is_not_a_calendar_window():
    assert CATALOG.resolve_population("POSTSEASON") == ["F", "D", "L", "W"]
    assert CATALOG.resolve_population("WORLD_SERIES") == ["W"]


def test_scope_gap_detects_narrower_actual():
    requested = Scope(season=2025, game_population="POSTSEASON")
    actual = Scope(season=2025, game_population="REGULAR_SEASON")
    gaps = compare_scope(requested, actual)
    assert any(gap.channel == "population" for gap in gaps)


def test_scope_gap_reports_broader_window():
    requested = Scope(date_range={"start": date(2025, 9, 1), "end": date(2025, 9, 30)})
    actual = Scope(date_range={"start": date(2025, 1, 1), "end": date(2025, 10, 1)})
    # A broader actual window must not satisfy a narrower request.
    gaps = compare_scope(requested, actual)
    assert any(gap.channel == "time" for gap in gaps)
