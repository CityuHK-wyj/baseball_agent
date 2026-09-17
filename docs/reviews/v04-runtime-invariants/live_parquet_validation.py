"""Live Parquet validation for the v0.4 Safe Analytical IR path.

Deterministic tests must pass first. This script runs the *real* compiled SQL against the
real Parquet archive with the real sandboxed DuckDB executor, and compares the result to
an independent reference calculation. It does not touch the network or PostgreSQL.

Run: PYTHONPATH=. python3 docs/reviews/v04-runtime-invariants/live_parquet_validation.py
"""

from __future__ import annotations

from datetime import date

import duckdb

from app.artifact_runtime.analytical_ir import Aggregate, AnalyticalQuery, Selection
from app.artifact_runtime.ir_compiler import compile_analytical_query
from app.artifact_runtime.schema_catalog import catalog_from_registry
from app.config import settings
from app.models.contracts import TimeRange
from app.tools.execution import DuckDBReadOnlyExecutor

TARGET_BATTER = 502671
WINDOW = TimeRange(start=date(2023, 4, 1), end=date(2023, 4, 30))


def main() -> int:
    path = f"{settings.parquet_archive_path}/mlb_statcast_*.parquet"
    query = AnalyticalQuery(
        query_id="live", source_kind="PARQUET", table="mlb_statcast_archive",
        selections=(
            Selection(alias="batter", kind="GROUP_KEY", field="batter"),
            Selection(alias="bbe", kind="AGGREGATE", aggregate=Aggregate(
                op="COUNT_NON_NULL", field="launch_speed", alias="bbe")),
            Selection(alias="avg_ev", kind="AGGREGATE", aggregate=Aggregate(
                op="AVG", field="launch_speed", alias="avg_ev")),
        ),
        date_field="game_date", window=WINDOW, min_rows=10, order_by="avg_ev", limit=200)
    compiled = compile_analytical_query(query, catalog_from_registry(),
                                        relation_sql=f"read_parquet('{path}')")
    print("compiled_ok:", compiled.ok, compiled.detail)
    print("coverage_status:", compiled.coverage_status)
    print("applied_window:", compiled.applied_window)
    print("window_predicate_present:",
          "game_date BETWEEN DATE '2023-04-01' AND DATE '2023-04-30'" in compiled.sql)

    executor = DuckDBReadOnlyExecutor(settings.parquet_archive_path)
    result, rows = executor.execute_with_rows(compiled.sql)
    print("execution_status:", result.status, result.error_code or "")
    produced = {int(row[0]): (int(row[1]), float(row[2])) for row in rows}
    print("target_from_compiled:", produced.get(TARGET_BATTER))
    print("rows_returned:", len(rows))

    connection = duckdb.connect(":memory:")
    try:
        reference = connection.execute(
            f"SELECT COUNT(launch_speed), AVG(launch_speed) FROM read_parquet('{path}') "
            "WHERE batter = ? AND game_date BETWEEN DATE '2023-04-01' "
            "AND DATE '2023-04-30'", [TARGET_BATTER]).fetchall()[0]
    finally:
        connection.close()
    reference_pair = (int(reference[0]), float(reference[1]))
    print("target_from_reference:", reference_pair)
    print("matches_reference:",
          produced.get(TARGET_BATTER) is not None
          and abs(produced[TARGET_BATTER][1] - reference_pair[1]) < 1e-9
          and produced[TARGET_BATTER][0] == reference_pair[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
