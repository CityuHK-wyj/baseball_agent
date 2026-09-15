"""Operational store: agent runtime metadata, separate from baseball analytics.

Stores versioned JSON objects (runs, objectives, requirements, plans, states, artifact
metadata, assessments, reports) and append-only checkpoints. The SQL is dialect-agnostic
(``ON CONFLICT`` upsert, positional rows); SQLite is the local/dev and test
implementation, and PostgreSQL is the production control plane behind the same Protocol.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from app.models.artifacts import ArtifactContract
from app.models.checkpoint import Checkpoint
from app.models.contracts import Name

_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS objects (
        kind TEXT NOT NULL,
        object_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (kind, object_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_objects_run_kind ON objects(run_id, kind)",
    """
    CREATE TABLE IF NOT EXISTS checkpoints (
        checkpoint_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_checkpoints_run ON checkpoints(run_id)",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StoredObject(ArtifactContract):
    kind: Name
    object_id: Name
    run_id: Name
    version: int = 0
    payload: dict = {}


class OperationalStore(Protocol):
    def replace_object(self, expected: StoredObject, payload: dict) -> bool: ...

    def save_object(self, kind: str, object_id: str, run_id: str, payload: dict) -> StoredObject: ...

    def get_object(self, kind: str, object_id: str) -> StoredObject | None: ...

    def list_objects(self, kind: str, run_id: str | None = None) -> tuple[StoredObject, ...]: ...

    def save_checkpoint(self, checkpoint: Checkpoint) -> None: ...

    def latest_checkpoint(self, run_id: str) -> Checkpoint | None: ...

    def list_checkpoints(self, run_id: str) -> tuple[Checkpoint, ...]: ...

    def close(self) -> None: ...


class SqlOperationalStore:
    """Dialect-agnostic store. Subclasses set ``placeholder`` and supply a connection."""

    placeholder = "?"

    def __init__(self, connection) -> None:
        self._connection = connection
        self._ensure_schema()

    # -- helpers ---------------------------------------------------------------
    def _execute(self, sql: str, params: tuple = ()):
        cursor = self._connection.cursor()
        cursor.execute(sql, params)
        return cursor

    def _ensure_schema(self) -> None:
        for statement in _SCHEMA_STATEMENTS:
            self._execute(statement)
        self._connection.commit()

    @staticmethod
    def _row_to_object(row) -> StoredObject:
        return StoredObject(kind=row[0], object_id=row[1], run_id=row[2],
                            version=row[3], payload=json.loads(row[4]))

    # -- objects ---------------------------------------------------------------
    def replace_object(self, expected: StoredObject, payload: dict) -> bool:
        """Atomically claim a version; only one competing resume may execute."""
        p = self.placeholder
        cursor = self._execute(
            f"UPDATE objects SET payload={p}, version=version+1, created_at={p} "
            f"WHERE kind={p} AND object_id={p} AND run_id={p} AND version={p}",
            (json.dumps(payload), _now(), expected.kind, expected.object_id,
             expected.run_id, expected.version))
        self._connection.commit()
        return cursor.rowcount == 1

    def save_object(self, kind: str, object_id: str, run_id: str, payload: dict) -> StoredObject:
        existing = self.get_object(kind, object_id)
        if existing is not None and existing.run_id != run_id:
            raise ValueError(
                f"Object identity ({kind}, {object_id}) already belongs to another run")
        if existing is not None and existing.payload == payload and existing.run_id == run_id:
            return existing
        version = existing.version + 1 if existing is not None else 0
        p = self.placeholder
        sql = (
            f"INSERT INTO objects (kind, object_id, run_id, version, payload, created_at) "
            f"VALUES ({p}, {p}, {p}, {p}, {p}, {p}) "
            f"ON CONFLICT(kind, object_id) DO UPDATE SET run_id=excluded.run_id, "
            f"version=excluded.version, payload=excluded.payload, created_at=excluded.created_at")
        self._execute(sql, (kind, object_id, run_id, version, json.dumps(payload), _now()))
        self._connection.commit()
        return self.get_object(kind, object_id)

    def get_object(self, kind: str, object_id: str) -> StoredObject | None:
        p = self.placeholder
        cursor = self._execute(
            f"SELECT kind, object_id, run_id, version, payload FROM objects "
            f"WHERE kind = {p} AND object_id = {p}", (kind, object_id))
        row = cursor.fetchone()
        return self._row_to_object(row) if row else None

    def list_objects(self, kind: str, run_id: str | None = None) -> tuple[StoredObject, ...]:
        p = self.placeholder
        if run_id is None:
            cursor = self._execute(
                f"SELECT kind, object_id, run_id, version, payload FROM objects "
                f"WHERE kind = {p} ORDER BY created_at, object_id", (kind,))
        else:
            cursor = self._execute(
                f"SELECT kind, object_id, run_id, version, payload FROM objects "
                f"WHERE kind = {p} AND run_id = {p} ORDER BY created_at, object_id", (kind, run_id))
        return tuple(self._row_to_object(row) for row in cursor.fetchall())

    # -- checkpoints -----------------------------------------------------------
    def save_checkpoint(self, checkpoint: Checkpoint) -> None:
        p = self.placeholder
        self._execute(
            f"INSERT INTO checkpoints (checkpoint_id, run_id, payload, created_at) "
            f"VALUES ({p}, {p}, {p}, {p})",
            (checkpoint.checkpoint_id, checkpoint.run_id,
             checkpoint.model_dump_json(), checkpoint.created_at.isoformat()))
        self._connection.commit()

    def latest_checkpoint(self, run_id: str) -> Checkpoint | None:
        p = self.placeholder
        cursor = self._execute(
            f"SELECT payload FROM checkpoints WHERE run_id = {p} "
            f"ORDER BY created_at DESC, checkpoint_id DESC LIMIT 1", (run_id,))
        row = cursor.fetchone()
        return Checkpoint.model_validate_json(row[0]) if row else None

    def list_checkpoints(self, run_id: str) -> tuple[Checkpoint, ...]:
        p = self.placeholder
        cursor = self._execute(
            f"SELECT payload FROM checkpoints WHERE run_id = {p} "
            f"ORDER BY created_at, checkpoint_id", (run_id,))
        return tuple(Checkpoint.model_validate_json(row[0]) for row in cursor.fetchall())

    def close(self) -> None:
        self._connection.close()


class SqliteOperationalStore(SqlOperationalStore):
    """Local/dev and test implementation. Not the production control plane."""

    placeholder = "?"

    def __init__(self, path: str | Path = ":memory:") -> None:
        super().__init__(sqlite3.connect(str(path)))


class PostgresOperationalStore(SqlOperationalStore):
    """Operational PostgreSQL control plane.

    Accepts an injected DB-API connection so callers control credentials and pooling.
    """

    placeholder = "%s"

    def __init__(self, connection) -> None:
        super().__init__(connection)
