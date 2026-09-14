"""Fail-closed Parquet entry point pending path sandboxing and SQL validation."""

import json

from app.config import Settings, settings


def query_local_cold_parquet(sql_query: str, config: Settings = settings) -> str:
    """Reject all unvalidated SQL without reading or writing any file."""
    return json.dumps({"error": "BLOCKED_BY_POLICY", "message": "Validated SQL execution is not yet available."})
