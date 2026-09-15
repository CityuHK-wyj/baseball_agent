"""Persistent Shared Knowledge store.

This is the truth source for long-lived MLB knowledge: structured, versioned, sourced
items plus a source registry, relations and snapshots. It is physically separate from the
read-only analytics plane and from the agent runtime object tables. SQLite is the local
and test implementation; PostgreSQL stores the same tables in a dedicated ``knowledge``
schema behind the same Protocol.

SQL is dialect-agnostic (``ON CONFLICT`` upsert, positional rows). Full-text retrieval is
a candidate filter plus deterministic ranking; no vector store is required.
"""

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Protocol

from app.models.knowledge import (
    KnowledgeItem,
    KnowledgeRelation,
    KnowledgeSnapshot,
    KnowledgeSource,
    KnowledgeStatus,
    KnowledgeType,
)

_COLUMNS = (
    "knowledge_id", "canonical_key", "knowledge_type", "status", "language", "version",
    "effective_from", "effective_to", "payload", "updated_at",
)

_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS {p}sources (
        source_id TEXT PRIMARY KEY,
        authority_level TEXT NOT NULL,
        source_type TEXT NOT NULL,
        active INTEGER NOT NULL,
        payload TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS {p}items (
        knowledge_id TEXT PRIMARY KEY,
        canonical_key TEXT NOT NULL,
        knowledge_type TEXT NOT NULL,
        status TEXT NOT NULL,
        language TEXT NOT NULL,
        version INTEGER NOT NULL,
        effective_from TEXT,
        effective_to TEXT,
        payload TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_{s}key ON {p}items(canonical_key)",
    "CREATE INDEX IF NOT EXISTS idx_{s}type ON {p}items(knowledge_type, status)",
    "CREATE INDEX IF NOT EXISTS idx_{s}status ON {p}items(status)",
    """
    CREATE TABLE IF NOT EXISTS {p}versions (
        knowledge_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        status TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (knowledge_id, version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS {p}relations (
        relation_id TEXT PRIMARY KEY,
        from_key TEXT NOT NULL,
        relation_type TEXT NOT NULL,
        to_key TEXT NOT NULL,
        payload TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_{s}rel_from ON {p}relations(from_key, relation_type)",
    "CREATE INDEX IF NOT EXISTS idx_{s}rel_to ON {p}relations(to_key, relation_type)",
    """
    CREATE TABLE IF NOT EXISTS {p}snapshots (
        snapshot_id TEXT PRIMARY KEY,
        label TEXT NOT NULL,
        activated INTEGER NOT NULL,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
)

_LIKE_ESCAPE = str.maketrans({"%": r"\%", "_": r"\_", "\\": r"\\"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


class KnowledgeStore(Protocol):
    def upsert_source(self, source: KnowledgeSource) -> KnowledgeSource: ...
    def get_source(self, source_id: str) -> KnowledgeSource | None: ...
    def list_sources(self, *, active_only: bool = False) -> tuple[KnowledgeSource, ...]: ...
    def upsert_item(self, item: KnowledgeItem) -> KnowledgeItem: ...
    def get_item(self, knowledge_id: str) -> KnowledgeItem | None: ...
    def get_by_canonical_key(self, canonical_key: str) -> KnowledgeItem | None: ...
    def list_items(self, *, knowledge_type: KnowledgeType | None = None,
                   status: KnowledgeStatus | None = None) -> tuple[KnowledgeItem, ...]: ...
    def history(self, knowledge_id: str) -> tuple[KnowledgeItem, ...]: ...
    def search_items(self, needle: str) -> tuple[KnowledgeItem, ...]: ...
    def item_count(self) -> int: ...
    def counts_by_type(self) -> dict[str, int]: ...
    def counts_by_status(self) -> dict[str, int]: ...
    def upsert_relation(self, relation: KnowledgeRelation) -> KnowledgeRelation: ...
    def relations(self, *, from_key: str | None = None, to_key: str | None = None,
                  relation_type: str | None = None) -> tuple[KnowledgeRelation, ...]: ...
    def save_snapshot(self, snapshot: KnowledgeSnapshot) -> KnowledgeSnapshot: ...
    def list_snapshots(self) -> tuple[KnowledgeSnapshot, ...]: ...
    def activate_snapshot(self, snapshot_id: str) -> KnowledgeSnapshot | None: ...
    def close(self) -> None: ...


class SqlKnowledgeStore:
    """Dialect-agnostic store. Subclasses set the table qualifier and connection."""

    placeholder = "?"
    _qualifier = "knowledge_"

    def __init__(self, connection) -> None:
        self._connection = connection
        self._ensure_schema()

    # -- helpers ---------------------------------------------------------------
    def _execute(self, sql: str, params: tuple = ()):
        cursor = self._connection.cursor()
        cursor.execute(sql, params)
        return cursor

    def _t(self, name: str) -> str:
        return f"{self._qualifier}{name}"

    def _ensure_schema(self) -> None:
        for statement in _SCHEMA_STATEMENTS:
            self._execute(statement.format(p=self._t(""), s="knowledge_"))
        self._connection.commit()

    @staticmethod
    def _row_to_item(row) -> KnowledgeItem:
        # Every item query selects only the JSON payload column.
        return KnowledgeItem.model_validate_json(row[0])

    # -- sources ---------------------------------------------------------------
    def upsert_source(self, source: KnowledgeSource) -> KnowledgeSource:
        existing = self.get_source(source.source_id)
        if existing is not None and existing.model_dump() == source.model_dump():
            return existing
        p = self.placeholder
        self._execute(
            f"INSERT INTO {self._t('sources')} "
            f"(source_id, authority_level, source_type, active, payload, updated_at) "
            f"VALUES ({p}, {p}, {p}, {p}, {p}, {p}) "
            f"ON CONFLICT(source_id) DO UPDATE SET authority_level=excluded.authority_level, "
            f"source_type=excluded.source_type, active=excluded.active, "
            f"payload=excluded.payload, updated_at=excluded.updated_at",
            (source.source_id, source.authority_level, source.source_type,
             int(source.active), source.model_dump_json(), _now()))
        self._connection.commit()
        return source

    def get_source(self, source_id: str) -> KnowledgeSource | None:
        p = self.placeholder
        row = self._execute(
            f"SELECT payload FROM {self._t('sources')} WHERE source_id = {p}", (source_id,)).fetchone()
        return KnowledgeSource.model_validate_json(row[0]) if row else None

    def list_sources(self, *, active_only: bool = False) -> tuple[KnowledgeSource, ...]:
        p = self.placeholder
        clause = f" WHERE active = {p}" if active_only else ""
        params = (1,) if active_only else ()
        rows = self._execute(
            f"SELECT payload FROM {self._t('sources')}{clause} ORDER BY source_id", params).fetchall()
        return tuple(KnowledgeSource.model_validate_json(row[0]) for row in rows)

    # -- items -----------------------------------------------------------------
    def upsert_item(self, item: KnowledgeItem) -> KnowledgeItem:
        existing = self.get_item(item.knowledge_id)
        if existing is not None and existing.model_dump(exclude={"version"}) == item.model_dump(exclude={"version"}):
            return existing
        version = existing.version + 1 if existing is not None else 1
        stored = item.model_copy(update={"version": version})
        p = self.placeholder
        with self._transaction():
            self._execute(
                f"INSERT INTO {self._t('items')} "
                f"(knowledge_id, canonical_key, knowledge_type, status, language, version, "
                f"effective_from, effective_to, payload, updated_at) "
                f"VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}) "
                f"ON CONFLICT(knowledge_id) DO UPDATE SET canonical_key=excluded.canonical_key, "
                f"knowledge_type=excluded.knowledge_type, status=excluded.status, "
                f"language=excluded.language, version=excluded.version, "
                f"effective_from=excluded.effective_from, effective_to=excluded.effective_to, "
                f"payload=excluded.payload, updated_at=excluded.updated_at",
                (stored.knowledge_id, stored.canonical_key, stored.knowledge_type, stored.status,
                 stored.language, stored.version, _iso(stored.effective_from),
                 _iso(stored.effective_to), stored.model_dump_json(), _now()))
            self._execute(
                f"INSERT INTO {self._t('versions')} "
                f"(knowledge_id, version, status, payload, created_at) VALUES ({p}, {p}, {p}, {p}, {p}) "
                f"ON CONFLICT(knowledge_id, version) DO UPDATE SET status=excluded.status, "
                f"payload=excluded.payload",
                (stored.knowledge_id, stored.version, stored.status,
                 stored.model_dump_json(), _now()))
        return stored

    def get_item(self, knowledge_id: str) -> KnowledgeItem | None:
        p = self.placeholder
        row = self._execute(
            f"SELECT payload FROM {self._t('items')} WHERE knowledge_id = {p}", (knowledge_id,)).fetchone()
        return self._row_to_item(row) if row else None

    def get_by_canonical_key(self, canonical_key: str) -> KnowledgeItem | None:
        p = self.placeholder
        row = self._execute(
            f"SELECT payload FROM {self._t('items')} WHERE canonical_key = {p} "
            f"ORDER BY version DESC", (canonical_key,)).fetchone()
        return self._row_to_item(row) if row else None

    def list_items(self, *, knowledge_type: KnowledgeType | None = None,
                   status: KnowledgeStatus | None = None) -> tuple[KnowledgeItem, ...]:
        clauses, params = [], []
        p = self.placeholder
        if knowledge_type is not None:
            clauses.append(f"knowledge_type = {p}")
            params.append(knowledge_type)
        if status is not None:
            clauses.append(f"status = {p}")
            params.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._execute(
            f"SELECT payload FROM {self._t('items')}{where} ORDER BY knowledge_id",
            tuple(params)).fetchall()
        return tuple(self._row_to_item(row) for row in rows)

    def history(self, knowledge_id: str) -> tuple[KnowledgeItem, ...]:
        p = self.placeholder
        rows = self._execute(
            f"SELECT payload FROM {self._t('versions')} WHERE knowledge_id = {p} "
            f"ORDER BY version", (knowledge_id,)).fetchall()
        return tuple(self._row_to_item(row) for row in rows)

    def search_items(self, needle: str) -> tuple[KnowledgeItem, ...]:
        """Candidate filter: canonical key, title and full payload (aliases, summary)."""
        escaped = needle.translate(_LIKE_ESCAPE)
        like = f"%{escaped}%"
        p = self.placeholder
        rows = self._execute(
            f"SELECT payload FROM {self._t('items')} "
            f"WHERE canonical_key LIKE {p} ESCAPE '\\' OR payload LIKE {p} ESCAPE '\\' "
            f"ORDER BY knowledge_id", (like, like)).fetchall()
        return tuple(self._row_to_item(row) for row in rows)

    def item_count(self) -> int:
        row = self._execute(f"SELECT COUNT(*) FROM {self._t('items')}").fetchone()
        return int(row[0]) if row else 0

    def counts_by_type(self) -> dict[str, int]:
        rows = self._execute(
            f"SELECT knowledge_type, COUNT(*) FROM {self._t('items')} "
            f"GROUP BY knowledge_type ORDER BY knowledge_type").fetchall()
        return {row[0]: int(row[1]) for row in rows}

    def counts_by_status(self) -> dict[str, int]:
        rows = self._execute(
            f"SELECT status, COUNT(*) FROM {self._t('items')} "
            f"GROUP BY status ORDER BY status").fetchall()
        return {row[0]: int(row[1]) for row in rows}

    # -- relations -------------------------------------------------------------
    def upsert_relation(self, relation: KnowledgeRelation) -> KnowledgeRelation:
        p = self.placeholder
        self._execute(
            f"INSERT INTO {self._t('relations')} "
            f"(relation_id, from_key, relation_type, to_key, payload, updated_at) "
            f"VALUES ({p}, {p}, {p}, {p}, {p}, {p}) "
            f"ON CONFLICT(relation_id) DO UPDATE SET from_key=excluded.from_key, "
            f"relation_type=excluded.relation_type, to_key=excluded.to_key, "
            f"payload=excluded.payload, updated_at=excluded.updated_at",
            (relation.relation_id, relation.from_key, relation.relation_type, relation.to_key,
             relation.model_dump_json(), _now()))
        self._connection.commit()
        return relation

    def relations(self, *, from_key: str | None = None, to_key: str | None = None,
                  relation_type: str | None = None) -> tuple[KnowledgeRelation, ...]:
        clauses, params = [], []
        p = self.placeholder
        for column, value in (("from_key", from_key), ("to_key", to_key),
                              ("relation_type", relation_type)):
            if value is not None:
                clauses.append(f"{column} = {p}")
                params.append(value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._execute(
            f"SELECT payload FROM {self._t('relations')}{where} ORDER BY relation_id",
            tuple(params)).fetchall()
        return tuple(KnowledgeRelation.model_validate_json(row[0]) for row in rows)

    # -- snapshots -------------------------------------------------------------
    def save_snapshot(self, snapshot: KnowledgeSnapshot) -> KnowledgeSnapshot:
        p = self.placeholder
        self._execute(
            f"INSERT INTO {self._t('snapshots')} "
            f"(snapshot_id, label, activated, payload, created_at) VALUES ({p}, {p}, {p}, {p}, {p}) "
            f"ON CONFLICT(snapshot_id) DO UPDATE SET label=excluded.label, "
            f"activated=excluded.activated, payload=excluded.payload",
            (snapshot.snapshot_id, snapshot.label, int(snapshot.activated),
             snapshot.model_dump_json(), snapshot.created_at.isoformat()))
        self._connection.commit()
        return snapshot

    def list_snapshots(self) -> tuple[KnowledgeSnapshot, ...]:
        rows = self._execute(
            f"SELECT payload FROM {self._t('snapshots')} "
            f"ORDER BY created_at, snapshot_id").fetchall()
        return tuple(KnowledgeSnapshot.model_validate_json(row[0]) for row in rows)

    def activate_snapshot(self, snapshot_id: str) -> KnowledgeSnapshot | None:
        p = self.placeholder
        row = self._execute(
            f"SELECT payload FROM {self._t('snapshots')} WHERE snapshot_id = {p}",
            (snapshot_id,)).fetchone()
        if row is None:
            return None
        snapshot = KnowledgeSnapshot.model_validate_json(row[0]).model_copy(update={"activated": True})
        self.save_snapshot(snapshot)
        return snapshot

    # -- transactions ----------------------------------------------------------
    class _Transaction:
        def __init__(self, connection, commit: bool) -> None:
            self._connection = connection
            self._commit = commit

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            if self._commit:
                self._connection.commit()
            else:
                self._connection.rollback()
            return False

    def _transaction(self) -> "_Transaction":
        return self._Transaction(self._connection, True)

    def close(self) -> None:
        self._connection.close()


class SqliteKnowledgeStore(SqlKnowledgeStore):
    """Local/dev and test knowledge store. Rebuildable from seed packs + manifests."""

    placeholder = "?"
    _qualifier = "knowledge_"

    def __init__(self, path: str | Path = ":memory:") -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        super().__init__(sqlite3.connect(str(path)))


class PostgresKnowledgeStore(SqlKnowledgeStore):
    """Production control-plane knowledge store in a dedicated ``knowledge`` schema.

    Accepts an injected DB-API connection so callers control credentials and pooling.
    The schema is created if missing and never mixes with ``baseball_analytics``.
    """

    placeholder = "%s"

    def __init__(self, connection, schema: str = "knowledge") -> None:
        self._schema = schema
        self._qualifier = f"{schema}."
        super().__init__(connection)

    def _ensure_schema(self) -> None:
        cursor = self._connection.cursor()
        cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {self._schema}")
        self._connection.commit()
        super()._ensure_schema()
