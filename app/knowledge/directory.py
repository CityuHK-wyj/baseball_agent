"""Player directory backed by the read-only PostgreSQL player_dictionary."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.capabilities.analytics.catalog import SOURCE_POSTGRES


class PlayerDirectory:
    def __init__(self, executor):
        self.executor = executor

    def search_by_name(self, fragment: str, limit: int = 5) -> list[dict[str, Any]]:
        cleaned = fragment.replace("'", "").strip()
        if not cleaned:
            return []
        tokens = [t for t in cleaned.replace(",", " ").split() if len(t) > 1]
        if not tokens:
            tokens = [cleaned]
        clauses = " AND ".join(f"player_name ILIKE '%{t}%'" for t in tokens)
        sql = (
            "SELECT player_id, player_name FROM public.player_dictionary "
            f"WHERE {clauses} ORDER BY player_name LIMIT {int(limit)}"
        )
        result = self.executor.run(sql, SOURCE_POSTGRES)
        if result.error:
            return []
        return [{"id": int(r["player_id"]), "name": r["player_name"]} for r in result.rows]

    def by_ids(self, ids: list[int]) -> dict[int, str]:
        if not ids:
            return {}
        safe = ", ".join(str(int(i)) for i in ids[:500])
        sql = f"SELECT player_id, player_name FROM public.player_dictionary WHERE player_id IN ({safe})"
        result = self.executor.run(sql, SOURCE_POSTGRES)
        if result.error:
            return {}
        return {int(r["player_id"]): r["player_name"] for r in result.rows}

    def exists(self, player_id: int) -> bool:
        sql = f"SELECT player_id FROM public.player_dictionary WHERE player_id = {int(player_id)} LIMIT 1"
        result = self.executor.run(sql, SOURCE_POSTGRES)
        return bool(result.rows)
