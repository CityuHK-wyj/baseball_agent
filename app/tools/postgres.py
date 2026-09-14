"""Read-only PostgreSQL entry point for the legacy tool interface.

SQL is validated by the AST guard before any connection is opened, then executed
in a read-only transaction with a bounded result and redacted errors.
"""

import json
from typing import Any

from app.config import Settings, settings
from app.tools.execution import PostgresReadOnlyExecutor

# Tables the agent runtime is permitted to read. Extend deliberately, never ad hoc.
ALLOWED_HOT_TABLES: tuple[str, ...] = ("statcast_pitches", "player_dictionary", "batting_events")


def connect(config: Settings = settings):
    """Open a guarded read-only connection. Validation happens before this is called."""
    import psycopg2
    return psycopg2.connect(
        host=config.postgres_host, port=config.postgres_port, dbname=config.postgres_db,
        user=config.postgres_user, password=config.postgres_password, connect_timeout=5)


def _serialize(rows: tuple) -> list:
    return [list(row) for row in rows]


def query_local_hot_db(sql_query: str, config: Settings = settings) -> str:
    """Validate, then execute a bounded read against the hot statcast store."""
    executor = PostgresReadOnlyExecutor(config, allowed_tables=ALLOWED_HOT_TABLES,
                                        connect_fn=connect)
    result, rows = executor.execute_with_rows(sql_query)
    if result.status == "ERROR":
        return json.dumps({"error": result.error_code or result.error_type,
                           "message": result.safe_error_summary}, ensure_ascii=False)
    return json.dumps({"row_count": result.row_count, "rows": _serialize(rows),
                       "metadata": list(result.execution_metadata)}, ensure_ascii=False)
