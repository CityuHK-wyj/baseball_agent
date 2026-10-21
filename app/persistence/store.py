"""Conversation persistence (SQLite). Artifacts live in memory; conversations persist."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.contracts.models import ConversationState


class PersistenceStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS conversations ("
                "session_id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.path))

    def save_conversation(self, state: ConversationState) -> None:
        payload = state.model_dump_json()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO conversations(session_id, payload, updated_at) VALUES(?,?,CURRENT_TIMESTAMP) "
                "ON CONFLICT(session_id) DO UPDATE SET payload=excluded.payload, updated_at=CURRENT_TIMESTAMP",
                (state.session_id, payload),
            )

    def load_conversation(self, session_id: str) -> ConversationState | None:
        with self._connect() as conn:
            row = conn.execute("SELECT payload FROM conversations WHERE session_id=?", (session_id,)).fetchone()
        if not row:
            return None
        try:
            return ConversationState.model_validate(json.loads(row[0]))
        except Exception:  # noqa: BLE001
            return None
