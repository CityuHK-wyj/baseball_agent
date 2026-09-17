"""Dialect-aware read-only SQL guard.

Parses SQL with sqlglot and allows only single read-only SELECT statements.
This is a structural control, not string matching: comments, casing, whitespace
and casing tricks cannot smuggle a mutation through.

It is intentionally a *guard*, not an executor. Wiring it to a live PostgreSQL
role and a DuckDB connection is ticket 02 (`02-verified-execution.md`).
"""

from dataclasses import dataclass
from glob import iglob
from pathlib import Path
from typing import Iterable

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError
from sqlglot.optimizer.scope import traverse_scope


@dataclass(frozen=True)
class GuardResult:
    allowed: bool
    reason: str = ""
    statement_kind: str = ""
    tables: tuple[str, ...] = ()
    file_paths: tuple[str, ...] = ()
    has_limit: bool = False


# Statement/nodes that mutate data, change schema, load code or open external state.
DENIED_NODE_NAMES = frozenset({
    "Drop", "Alter", "AlterTable", "AlterColumn", "TruncateTable", "Attach", "Detach",
    "Install", "Load", "Pragma", "Set", "Use", "Grant", "Revoke", "Command",
    "Transaction", "Commit", "Rollback", "Into", "Analyze", "Comment", "Refresh",
    "Cache", "Uncache", "Export", "Kill", "Vacuum", "Create",
})

# Functions that read or write arbitrary files/processes/extensions.
DEFAULT_FORBIDDEN_FUNCTIONS = frozenset({
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file",
    "lo_import", "lo_export", "dblink", "pg_sleep", "query_to_xml",
    "read_text", "read_blob", "write_file", "write_text", "write_blob", "copy_file",
    "shell", "system", "getenv", "setenv", "load_extension",
    "query", "query_table", "json_execute_serialized_sql", "read_duckdb",
})

# Functions that read data files. Allowed only under an explicit file root.
FILE_READING_FUNCTIONS = frozenset({
    "read_parquet", "parquet_scan", "read_csv", "read_csv_auto", "read_json",
    "read_json_auto", "read_ndjson", "read_ndjson_auto", "read_xlsx", "glob",
    "read_json_objects", "read_json_objects_auto", "read_ndjson_objects", "sniff_csv",
    "parquet_metadata", "parquet_schema", "parquet_file_metadata", "parquet_full_metadata",
    "parquet_kv_metadata", "parquet_bloom_probe",
})

_SELECT_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery)


def resolve_within_root(path: str, root: str | Path) -> bool:
    """True only if a (possibly glob) path stays inside an explicit root."""
    # A URI is external state, not a relative path below the archive root.  In
    # particular, treating ``https://...`` as a Path would make it look relative.
    if "://" in path or path.casefold().startswith("file:"):
        return False
    root_path = Path(root).resolve()
    candidate = Path(path)
    if ".." in candidate.parts:
        return False
    if not candidate.is_absolute():
        # DuckDB interprets relative paths against the process working directory.
        candidate = Path.cwd() / candidate
    literal_parts: list[str] = []
    for part in candidate.parts:
        if any(token in part for token in "*?["):
            break
        literal_parts.append(part)
    prefix = Path(*literal_parts) if literal_parts else root_path
    resolved = prefix.resolve()
    if resolved != root_path and root_path not in resolved.parents:
        return False
    # A safe glob prefix can still match a symlink to an external file.
    return all(root_path == target or root_path in target.parents
               for match in iglob(str(candidate), recursive=True)
               for target in (Path(match).resolve(),))


def _function_name(node: exp.Expression) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.this)
    name = node.sql_name() if hasattr(node, "sql_name") else type(node).__name__
    return name


def _function_args(node: exp.Expression) -> list[exp.Expression]:
    return list(node.expressions)


def _string_literals(node: exp.Expression) -> list[str]:
    return [item.this for item in _function_args(node)
            if isinstance(item, exp.Literal) and item.is_string]


def _file_paths(node: exp.Expression) -> tuple[str, ...] | None:
    """Extract only statically-known file arguments from a reader call.

    A file reader may take one literal path or a literal list of paths.  Dynamic SQL
    expressions cannot be proven to stay in the sandbox, so they are rejected rather
    than deferred to DuckDB.
    """
    paths: list[str] = []

    def collect(item: exp.Expression) -> bool:
        if isinstance(item, exp.Literal) and item.is_string:
            paths.append(item.this)
            return True
        if isinstance(item, exp.Array):
            return bool(item.expressions) and all(collect(child) for child in item.expressions)
        return False

    arguments = _function_args(node)
    if not arguments or not all(collect(item) for item in arguments):
        return None
    return tuple(paths)


def _referenced_tables(tree: exp.Expression) -> tuple[str, ...]:
    names: list[str] = []
    for scope in traverse_scope(tree):
        for table in scope.tables:
            if not isinstance(table.this, (exp.Identifier, exp.Dot)):
                continue
            # Qualified tables always refer to physical relations. Unqualified
            # CTE references are resolved only in their actual lexical scope.
            source = scope.sources.get(table.alias_or_name)
            if not table.db and not table.catalog and not isinstance(source, exp.Table):
                continue
            names.append(".".join(part for part in (table.catalog, table.db, table.name) if part))
    return tuple(dict.fromkeys(names))


def guard_read_only_sql(sql: str, *, dialect: str = "postgres",
                        allowed_tables: Iterable[str] | None = None,
                        file_root: str | Path | None = None,
                        forbidden_functions: Iterable[str] = DEFAULT_FORBIDDEN_FUNCTIONS) -> GuardResult:
    if not sql or not sql.strip():
        return GuardResult(False, "Empty SQL.")
    try:
        statements = sqlglot.parse(sql, read=dialect, error_level=sqlglot.ErrorLevel.RAISE)
    except ParseError as error:
        return GuardResult(False, f"Unparseable SQL: {error}")
    statements = [statement for statement in statements if statement is not None]
    if len(statements) != 1:
        return GuardResult(False, "Exactly one statement is allowed.")
    tree = statements[0]
    if not isinstance(tree, _SELECT_ROOTS):
        return GuardResult(False, f"Statement type {type(tree).__name__} is not read-only.")

    forbidden = {name.lower() for name in forbidden_functions}
    for node in tree.walk():
        if type(node).__name__ in DENIED_NODE_NAMES or isinstance(node, (exp.DDL, exp.DML)):
            return GuardResult(False, f"Node {type(node).__name__} is not permitted.")

    file_paths: list[str] = []
    for node in tree.walk():
        parent_function = isinstance(node, (exp.Func, exp.Anonymous)) and not isinstance(node, exp.Table)
        if not parent_function:
            continue
        name = _function_name(node).lower()
        if name in forbidden:
            return GuardResult(False, f"Function {name} is not permitted.")
        if name in FILE_READING_FUNCTIONS:
            paths = _string_literals(node)
            if file_root is None:
                return GuardResult(False, f"Function {name} requires an explicit file root.")
            paths = _file_paths(node)
            if paths is None:
                return GuardResult(False, f"Function {name} requires literal sandboxed file paths.")
            for candidate in paths:
                if not resolve_within_root(candidate, file_root):
                    return GuardResult(False, f"Path escapes the permitted root: {candidate}")
                file_paths.append(candidate)

    # DuckDB accepts a quoted table expression as a file scan (for example
    # ``FROM '/tmp/data.parquet'``). It is not represented as a file-reading
    # function in sqlglot, so reject it before opening the connection.
    if dialect == "duckdb":
        for table in tree.find_all(exp.Table):
            identifier = table.this
            if isinstance(identifier, exp.Identifier) and identifier.args.get("quoted"):
                return GuardResult(False, "Quoted DuckDB table expressions are not permitted.")

    tables = _referenced_tables(tree)
    if allowed_tables is not None:
        permitted = {name.lower() for name in allowed_tables}
        disallowed = [name for name in tables if name.lower() not in permitted]
        if disallowed:
            return GuardResult(False, f"Tables not in the allowlist: {sorted(disallowed)}")

    return GuardResult(True, statement_kind=type(tree).__name__.upper(), tables=tables,
                       file_paths=tuple(file_paths), has_limit=tree.args.get("limit") is not None)
