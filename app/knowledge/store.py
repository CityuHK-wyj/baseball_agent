"""Shared Knowledge: persistent ACTIVE knowledge with an explicit governance split.

Read path:  ACTIVE Knowledge -> Knowledge Artifact -> runtime.
Write path: runtime discovery -> CandidateKnowledge -> (admin review) -> ACTIVE.

The MVP reads a curated seed and writes candidates to a local candidate journal. Runtime
evidence is never auto-promoted into ACTIVE knowledge.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "knowledge"

# WSA-07 / GA-08: the stable/mutable split. Stable semantic knowledge (definitions, aliases,
# terminology, rules, stable identity) may assist interpretation and ground definition/identity
# obligations. Mutable factual state is *evidence*: it must re-enter the normal evidence path
# and can never be asserted merely because it is cached in Shared Knowledge.
SEMANTIC_KNOWLEDGE_TYPES = frozenset({
    "DEFINITION", "ENTITY_ALIAS", "ALIAS", "RULE", "TERMINOLOGY", "IDENTITY",
    "METRIC", "GLOSSARY", "REFERENCE",
})
MUTABLE_KNOWLEDGE_TYPES = frozenset({
    "FACT", "STATUS", "INJURY", "INJURY_STATUS", "TRANSACTION", "CONTRACT", "ROSTER",
    "ROSTER_STATUS", "NEWS", "CURRENT", "CURRENT_STATE", "PERFORMANCE", "SALARY", "ROLE",
})


def knowledge_class(item_type: str) -> str:
    """Classify a KnowledgeItem type as stable semantic knowledge or mutable factual state."""
    normalized = (item_type or "").strip().upper()
    if normalized in SEMANTIC_KNOWLEDGE_TYPES:
        return "SEMANTIC"
    if normalized in MUTABLE_KNOWLEDGE_TYPES:
        return "MUTABLE_FACT"
    return "UNKNOWN"


@dataclass
class KnowledgeItem:
    id: str
    type: str
    title: str
    aliases: list[str] = field(default_factory=list)
    language: str = "en"
    summary: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    authority: str = "OFFICIAL"
    status: str = "ACTIVE"
    source_refs: list[str] = field(default_factory=list)

    @property
    def knowledge_class(self) -> str:
        return knowledge_class(self.type)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "knowledge_class": self.knowledge_class,
            "title": self.title,
            "aliases": self.aliases,
            "language": self.language,
            "summary": self.summary,
            "payload": self.payload,
            "authority": self.authority,
            "status": self.status,
            "source_refs": self.source_refs,
        }


class KnowledgeStore:
    def __init__(self, seed_dir: Path | None = None, candidate_path: Path | None = None, active_path: Path | None = None):
        self.seed_dir = seed_dir or DATA_DIR
        self.candidate_path = candidate_path
        self.active_path = active_path
        self.items: list[KnowledgeItem] = []
        self._alias_index: dict[str, KnowledgeItem] = {}
        self._load()

    def _load(self) -> None:
        self.items = []
        self._alias_index = {}
        if self.seed_dir.exists():
            for path in sorted(self.seed_dir.glob("*.json")):
                self._load_items_from(path)
        if self.active_path and self.active_path.exists():
            self._load_items_from(self.active_path)

    def _load_items_from(self, path: Path) -> None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        for raw in payload.get("items", []):
            if raw.get("status", "ACTIVE") != "ACTIVE":
                continue
            item = KnowledgeItem(
                id=raw["id"],
                type=raw.get("type", "DEFINITION"),
                title=raw.get("title", ""),
                aliases=[a for a in raw.get("aliases", []) if a],
                language=raw.get("language", "en"),
                summary=raw.get("summary", ""),
                payload=raw.get("payload", {}),
                authority=raw.get("authority", "OFFICIAL"),
                status=raw.get("status", "ACTIVE"),
                source_refs=raw.get("source_refs", []),
            )
            self.items.append(item)
            for alias in [item.title, *item.aliases]:
                self._alias_index.setdefault(_normalize(alias), item)

    # -- reads --------------------------------------------------------------
    def match_alias(self, text: str) -> KnowledgeItem | None:
        return self._alias_index.get(_normalize(text))

    def search(self, query: str, limit: int = 5) -> list[KnowledgeItem]:
        needle = _normalize(query)
        if not needle:
            return []
        scored: list[tuple[int, KnowledgeItem]] = []
        for item in self.items:
            haystacks = [item.title, *item.aliases, item.summary]
            if any(needle in _normalize(h) for h in haystacks if h):
                scored.append((0, item))
            elif any(_normalize(h) in needle for h in haystacks if h and len(h) > 1):
                scored.append((1, item))
        scored.sort(key=lambda pair: pair[0])
        return [item for _, item in scored[:limit]]

    def semantic_context(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        """Bounded stable interpretation context, never mutable factual authority."""
        items = [item for item in self.search(query, limit=max(len(self.items), 1))
                 if item.knowledge_class == "SEMANTIC" and item.status == "ACTIVE"]
        taxonomy = [item for item in self.items if item.id in {"semantic_evidence_intents", "semantic_native_records"}
                    and item.knowledge_class == "SEMANTIC" and item.status == "ACTIVE"]
        selected = {item.id: item for item in [*taxonomy, *items]}
        return [{"id": item.id, "type": item.type, "title": item.title,
                 "aliases": list(item.aliases)[:12], "definition": item.summary[:600]}
                for item in list(selected.values())[:max(0, min(limit, 12))]]

    def by_type(self, item_type: str) -> list[KnowledgeItem]:
        return [i for i in self.items if i.type == item_type]

    # -- candidate knowledge (never ACTIVE automatically) -------------------
    def record_candidate(self, item: dict[str, Any], *, origin: str, evidence: list[str]) -> str:
        if not self.candidate_path:
            return ""
        self.candidate_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_id = f"cand_{abs(hash((item.get('title', ''), origin))) % 10**10:010d}"
        record = {
            "candidate_id": candidate_id,
            "status": "CANDIDATE",
            "proposed": item,
            "origin": origin,
            "evidence": evidence,
        }
        self._append_candidate_record(record)
        return candidate_id

    def _append_candidate_record(self, record: dict[str, Any]) -> None:
        if not self.candidate_path:
            return
        self.candidate_path.parent.mkdir(parents=True, exist_ok=True)
        with self.candidate_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def list_candidates(self) -> list[dict[str, Any]]:
        if not self.candidate_path or not self.candidate_path.exists():
            return []
        merged: dict[str, dict[str, Any]] = {}
        for line in self.candidate_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            candidate_id = record.get("candidate_id")
            if not candidate_id:
                continue
            current = merged.get(candidate_id, {})
            current.update(record)
            merged[candidate_id] = current
        return list(merged.values())

    def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        for record in self.list_candidates():
            if record.get("candidate_id") == candidate_id:
                return record
        return None

    def approve_candidate(self, candidate_id: str, *, reviewer: str = "admin") -> bool:
        record = self.get_candidate(candidate_id)
        if not record or record.get("status") not in {"CANDIDATE", "UNDER_REVIEW"}:
            return False
        item = dict(record.get("proposed") or {})
        item.setdefault("status", "ACTIVE")
        item.setdefault("authority", "REVIEWED")
        if not self.active_path:
            return False
        payload = {"items": []}
        if self.active_path.exists():
            try:
                payload = json.loads(self.active_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = {"items": []}
        items = [i for i in payload.get("items", []) if i.get("id") != item.get("id")]
        items.append(item)
        self.active_path.parent.mkdir(parents=True, exist_ok=True)
        self.active_path.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=2), encoding="utf-8")
        self._append_candidate_record({"candidate_id": candidate_id, "status": "APPROVED", "reviewer": reviewer})
        self._load()
        return True

    def reject_candidate(self, candidate_id: str, *, reason: str = "", reviewer: str = "admin") -> bool:
        record = self.get_candidate(candidate_id)
        if not record or record.get("status") not in {"CANDIDATE", "UNDER_REVIEW"}:
            return False
        self._append_candidate_record({"candidate_id": candidate_id, "status": "REJECTED", "reviewer": reviewer, "reason": reason})
        return True


def _normalize(text: str) -> str:
    return re.sub(r"[\s\.,，。·\-_]+", "", (text or "").strip().lower())
