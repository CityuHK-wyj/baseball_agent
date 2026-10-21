"""Read-only SQL guard: defense in depth over the deterministic compiler.

The compiler never emits anything but a single SELECT, but the guard independently
parses the SQL and rejects any non-read statement, multi-statement input, comments or
forbidden functions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

# Keyword tokens are matched on word boundaries so that values such as 'truncated_pa'
# (a real Statcast event) are never mistaken for a 'truncate' statement.
FORBIDDEN_KEYWORDS = (
    "insert", "update", "delete", "drop", "alter", "create", "truncate", "grant",
    "revoke", "copy", "vacuum", "analyze", "attach", "detach", "pragma", "call",
    "install", "load", "set", "reset",
)
FORBIDDEN_SUBSTRINGS = (
    "pg_read_file", "pg_ls_dir", "lo_import", "lo_export", "dblink", "information_schema",
    "pg_catalog", "pg_shadow", "pg_authid", "duckdb_settings", "read_csv", "read_json",
    "read_text", "glob(", "httpfs",
)
_FORBIDDEN_KEYWORD_RE = re.compile(r"\b(" + "|".join(FORBIDDEN_KEYWORDS) + r")\b", re.IGNORECASE)


@dataclass
class GuardResult:
    ok: bool
    reason: str = ""


def guard_sql(sql: str) -> GuardResult:
    text = (sql or "").strip()
    if not text:
        return GuardResult(False, "empty SQL")
    if "--" in text or "/*" in text:
        return GuardResult(False, "SQL comments are not allowed")
    lowered = text.lower()
    keyword = _FORBIDDEN_KEYWORD_RE.search(text)
    if keyword:
        return GuardResult(False, f"forbidden token '{keyword.group(0)}'")
    for token in FORBIDDEN_SUBSTRINGS:
        if token in lowered:
            return GuardResult(False, f"forbidden token '{token.strip()}'")
    if text.rstrip().rstrip(";").count(";") > 0:
        return GuardResult(False, "multiple statements are not allowed")
    try:
        statements = sqlglot.parse(text, read="postgres")
    except Exception as exc:  # noqa: BLE001
        return GuardResult(False, f"unparseable SQL: {type(exc).__name__}")
    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        return GuardResult(False, f"expected exactly one statement, found {len(statements)}")
    statement = statements[0]
    if not isinstance(statement, exp.Select):
        return GuardResult(False, f"only SELECT statements are allowed, found {type(statement).__name__}")
    return GuardResult(True, "")
