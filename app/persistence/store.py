"""Operational store: agent runtime metadata, separate from baseball analytics.

Stores versioned JSON objects (runs, objectives, requirements, plans, states,
artifacts metadata, assessments, reports) and append-only checkpoints. The first
implementation uses SQLite so it is local, testable and replaceable by an
Operational PostgreSQL implementation of the same Protocol.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from app.models.artifacts import ArtifactContract
from app.models.checkpoint import Checkpoint
from app.models.contracts import Name

_SCHEMA = """
CREATE TABLE IF NOT EXISTS objects (
    kind TEXT NOT NULL,
    object_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (kind, object_id)
);
CREATE INDEX IF NOT EXISTS idx_objects_run_kind ON objects(run_id, kind);
CREATE TABLE IF NOT EXISTS checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_checkpoints_run ON checkpoints(run_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StoredObject(ArtifactContract):
    kind: Name
    object_id: Name
    run_id: Name
    version: int = 0
    payload: dict = {}


class OperationalStore(Protocol):
    def save_object(self, kind: str, object_id: str, run_id: str, payload: dict) -> StoredObject: ...

    def get_object(self, kind: str, object_id: str) -> StoredObject | None: ...

    def list_objects(self, kind: str, run_id: str | None = None) -> tuple[StoredObject, ...]: ...

    def save_checkpoint(self, checkpoint: Checkpoint) -> None: ...

    def latest_checkpoint(self, run_id: str) -> Checkpoint | None: ...

    def list_checkpoints(self, run_id: str) -> tuple[Checkpoint, ...]: ...

    def close(self) -> None: ...


class SqliteOperationalStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(path))
        self._connection.row_factory = sqlite3.Row
        self._connection.executescript(_SCHEMA)
        self._connection.commit()

    def save_object(self, kind: str, object_id: str, run_id: str, payload: dict) -> StoredObject:
        existing = self.get_object(kind, object_id)
        if existing is not None and existing.payload == payload and existing.run_id == run_id:
            return existing
        version = existing.version + 1 if existing is not None else 0
        self._connection.execute(
            "INSERT INTO objects (kind, object_id, run_id, version, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(kind, object_id) DO UPDATE SET "
            "run_id=excluded.run_id, version=excluded.version, payload=excluded.payload, "
            "created_at=excluded.created_at",
            (kind, object_id, run_id, version, json.dumps(payload), _now()))
        self._connection.commit()
        return self.get_object(kind, object_id)

    def get_object(self, kind: str, object_id: str) -> StoredObject | None:
        row = self._connection.execute(
            "SELECT kind, object_id, run_id, version, payload FROM objects "
            "WHERE kind = ? AND object_id = ?", (kind, object_id)).fetchone()
        return self._row_to_object(row) if row else None

    def list_objects(self, kind: str, run_id: str | None = None) -> tuple[StoredObject, ...]:
        if run_id is None:
            rows = self._connection.execute(
                "SELECT kind, object_id, run_id, version, payload FROM objects "
                "WHERE kind = ? ORDER BY rowid", (kind,)).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT kind, object_id, run_id, version, payload FROM objects "
                "WHERE kind = ? AND run_id = ? ORDER BY rowid", (kind, run_id)).fetchall()
        return tuple(self._row_to_object(row) for row in rows)

    @staticmethod
    def _row_to_object(row: sqlite3.Row) -> StoredObject:
        return StoredObject(kind=row["kind"], object_id=row["object_id"], run_id=row["run_id"],
                            version=row["version"], payload=json.loads(row["payload"]))

    def save_checkpoint(self, checkpoint: Checkpoint) -> None:
        self._connection.execute(
            "INSERT INTO checkpoints (checkpoint_id, run_id, payload, created_at) VALUES (?, ?, ?, ?)",
            (checkpoint.checkpoint_id, checkpoint.run_id,
             checkpoint.model_dump_json(), checkpoint.created_at.isoformat()))
        self._connection.commit()

    def latest_checkpoint(self, run_id: str) -> Checkpoint | None:
        row = self._connection.execute(
            "SELECT payload FROM checkpoints WHERE run_id = ? ORDER BY rowid DESC LIMIT 1",
            (run_id,)).fetchone()
        return Checkpoint.model_validate_json(row["payload"]) if row else None

    def list_checkpoints(self, run_id: str) -> tuple[Checkpoint, ...]:
        rows = self._connection.execute(
            "SELECT payload FROM checkpoints WHERE run_id = ? ORDER BY rowid", (run_id,)).fetchall()
        return tuple(Checkpoint.model_validate_json(row["payload"]) for row in rows)

    def close(self) -> None:
        self._connection.close()
