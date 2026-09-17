"""Read-only DuckDB/Parquet entry point for the legacy tool interface.

Every path is validated to stay inside the configured archive root before any
DuckDB connection is opened. Extension loading, ATTACH, COPY and mutation are
rejected by the AST guard.
"""

import json

from app.config import Settings, settings
from app.tools.execution import DuckDBReadOnlyExecutor


def query_local_cold_parquet(sql_query: str, config: Settings = settings) -> str:
    """Validate, then execute a bounded read against the Parquet archive."""
    executor = DuckDBReadOnlyExecutor(config.parquet_archive_path)
    result, rows = executor.execute_with_rows(sql_query)
    if result.status == "ERROR":
        return json.dumps({"error": result.error_code or result.error_type,
                           "message": result.safe_error_summary}, ensure_ascii=False)
    return json.dumps({"row_count": result.row_count, "rows": [list(row) for row in rows],
                       "metadata": list(result.execution_metadata)}, ensure_ascii=False)
