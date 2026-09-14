"""Fail-closed PostgreSQL entry points pending the read-only SQL executor."""

import json

from app.config import Settings, settings


def connect(config: Settings = settings):
    """Do not open unguarded analytics connections."""
    raise PermissionError("Validated read-only PostgreSQL execution is not yet available.")


def query_local_hot_db(sql_query: str, config: Settings = settings) -> str:
    """Reject all unvalidated SQL without opening a connection."""
    return json.dumps({"error": "BLOCKED_BY_POLICY", "message": "Validated SQL execution is not yet available."})
