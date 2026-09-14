"""Guarded read-only Parquet summary.

Importing this module has no side effects and opens no connection. The previous
raw, unguarded DuckDB query was removed: all analytical reads must go through the
AST guard in ``app.validation.sql_guard``.
"""

from app.config import settings
from app.tools.execution import DuckDBReadOnlyExecutor


def build_query() -> str:
    pattern = settings.parquet_archive_path / "mlb_statcast_*.parquet"
    return f"SELECT COUNT(*) AS pitch_count FROM read_parquet('{pattern}')"


def run() -> int:
    executor = DuckDBReadOnlyExecutor(settings.parquet_archive_path)
    result, rows = executor.execute_with_rows(build_query())
    if result.status == "ERROR":
        print(f"Read blocked or failed: {result.safe_error_summary}")
        return 1
    print(f"rows={result.row_count} pitch_count={rows[0][0] if rows else 0}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
