"""Knowledge ingestion: fetch/normalize -> validate -> stage -> activate.

Nothing fetched from the web becomes approved knowledge directly. A pack is validated
deterministically, staged as ``COLLECTED`` (invisible to retrieval), and only then
activated. Activation also supersedes domain items that disappeared from the pack, so a
refresh replaces a category instead of silently mixing old and new facts.
"""

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable

from app.models.knowledge import (
    KnowledgeDiff,
    KnowledgeItem,
    KnowledgeRelation,
    KnowledgeSnapshot,
    KnowledgeSource,
)

DOMAIN_TAG_PREFIX = "domain:"


class KnowledgePackError(ValueError):
    """Fatal pack-level problem; nothing is ingested."""


@dataclass(frozen=True)
class KnowledgePack:
    domain: str
    items: tuple[KnowledgeItem, ...] = ()
    relations: tuple[KnowledgeRelation, ...] = ()
    sources: tuple[KnowledgeSource, ...] = ()
    expected_team_keys: tuple[str, ...] = ()
    path: str = ""


def load_pack(path: str | Path) -> KnowledgePack:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return pack_from_dict(payload, path=str(path))


def pack_from_dict(payload: dict, path: str = "") -> KnowledgePack:
    return KnowledgePack(
        domain=payload["domain"],
        items=tuple(KnowledgeItem.model_validate(entry) for entry in payload.get("items", ())),
        relations=tuple(KnowledgeRelation.model_validate(entry) for entry in payload.get("relations", ())),
        sources=tuple(KnowledgeSource.model_validate(entry) for entry in payload.get("sources", ())),
        expected_team_keys=tuple(payload.get("expected_team_keys", ())),
        path=path,
    )


def _domain_tag(domain: str) -> str:
    return f"{DOMAIN_TAG_PREFIX}{domain}"


@dataclass
class ValidationReport:
    fatals: tuple[str, ...] = ()
    rejected: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.fatals


class KnowledgeValidator:
    """Deterministic structural validation, per knowledge type."""

    def __init__(self, known_source_ids: Iterable[str] = (), known_keys: Iterable[str] = ()) -> None:
        self._sources = set(known_source_ids)
        self._keys = set(known_keys)

    def validate(self, pack: KnowledgePack) -> ValidationReport:
        fatals: list[str] = []
        rejected: dict[str, str] = {}

        ids = [item.knowledge_id for item in pack.items]
        if len(set(ids)) != len(ids):
            fatals.append("duplicate knowledge_id within pack")
        keys = [item.canonical_key for item in pack.items]
        if len(set(keys)) != len(keys):
            fatals.append("duplicate canonical_key within pack")

        if pack.expected_team_keys:
            actual = {item.canonical_key for item in pack.items if item.knowledge_type == "TEAM"}
            expected = set(pack.expected_team_keys)
            if actual != expected:
                fatals.append(
                    f"team set mismatch: missing={sorted(expected - actual)} unknown={sorted(actual - expected)}")

        for item in pack.items:
            problem = self._validate_item(item)
            if problem:
                rejected[item.knowledge_id] = problem

        pack_keys = set(keys) | set(ids) | set(self._keys)
        for relation in pack.relations:
            if relation.from_key not in pack_keys or relation.to_key not in pack_keys:
                fatals.append(f"relation {relation.relation_id} references an unknown key")

        return ValidationReport(tuple(fatals), rejected)

    def _validate_item(self, item: KnowledgeItem) -> str:
        if item.effective_from and item.effective_to and item.effective_to < item.effective_from:
            return "effective_to precedes effective_from"
        unknown = [ref for ref in item.source_refs if ref not in self._sources]
        if unknown:
            return f"unknown source_refs: {sorted(unknown)}"
        if item.knowledge_type == "TEAM":
            payload = item.structured_payload
            for field_name in ("league", "division", "abbreviation", "official_name"):
                if not payload.get(field_name):
                    return f"TEAM item missing structured field {field_name!r}"
        elif item.knowledge_type == "RULE":
            if not item.structured_payload.get("rule_reference"):
                return "RULE item missing rule_reference"
        elif item.knowledge_type == "METRIC":
            payload = item.structured_payload
            if not payload.get("definition") or not payload.get("provider"):
                return "METRIC item missing definition/provider"
        elif item.knowledge_type == "COMMUNITY_CREATOR":
            # Community profiles may be professional outlets (TRUSTED_ANALYTICS) or forums
            # (COMMUNITY); authority is carried by the referenced source, not forced here.
            if not item.structured_payload:
                return "COMMUNITY_CREATOR missing structured profile"
        elif item.knowledge_type == "SOURCE":
            if not item.source_refs:
                return "SOURCE item must reference its registry source"
        return ""


class KnowledgeIngester:
    def __init__(self, store, today: Callable[[], date] = date.today,
                 snapshot_id_factory: Callable[[str], str] | None = None) -> None:
        self._store = store
        self._today = today
        self._snapshot_id = snapshot_id_factory or (
            lambda domain: f"snap-{domain}-{datetime.now().strftime('%Y%m%d%H%M%S')}")

    def ingest(self, pack: KnowledgePack, *, activate: bool = True) -> KnowledgeDiff:
        validator = KnowledgeValidator(
            known_source_ids={source.source_id for source in self._store.list_sources()}
            | {source.source_id for source in pack.sources},
            known_keys=[item.canonical_key for item in self._store.list_items()]
            + [item.knowledge_id for item in self._store.list_items()])
        report = validator.validate(pack)
        if not report.ok:
            raise KnowledgePackError("; ".join(report.fatals))

        added, updated, unchanged = [], [], []
        for source in pack.sources:
            self._store.upsert_source(source)

        kept: list[KnowledgeItem] = []
        for item in pack.items:
            if item.knowledge_id in report.rejected:
                continue
            tagged = self._tag(item, pack.domain)
            existing = self._store.get_item(tagged.knowledge_id)
            if existing is None:
                added.append(tagged.knowledge_id)
            elif existing.model_dump(exclude={"version"}) == tagged.model_dump(exclude={"version"}):
                unchanged.append(tagged.knowledge_id)
            else:
                updated.append(tagged.knowledge_id)
            self._store.upsert_item(self._staged(tagged))
            kept.append(tagged)

        if activate:
            for item in kept:
                self._store.upsert_item(item)
            self._supersede_missing(pack, kept)

        for relation in pack.relations:
            self._store.upsert_relation(relation)

        if activate:
            self._store.save_snapshot(KnowledgeSnapshot(
                snapshot_id=self._snapshot_id(pack.domain), label=pack.domain,
                counts=self._store.counts_by_type(),
                source_ids=tuple(item.source_id for item in pack.sources), activated=True))

        return KnowledgeDiff(domain=pack.domain, added=tuple(added), updated=tuple(updated),
                             unchanged=tuple(unchanged), rejected=tuple(report.rejected),
                             ok=not report.rejected,
                             message="" if not report.rejected else "some items were rejected")

    def _staged(self, item: KnowledgeItem) -> KnowledgeItem:
        return item.model_copy(update={"status": "COLLECTED", "verification_status": "COLLECTED"})

    def _tag(self, item: KnowledgeItem, domain: str) -> KnowledgeItem:
        tag = _domain_tag(domain)
        if tag in item.tags:
            return item
        return item.model_copy(update={"tags": (*item.tags, tag)})

    def _supersede_missing(self, pack: KnowledgePack, kept: list[KnowledgeItem]) -> None:
        tag = _domain_tag(pack.domain)
        current = {item.knowledge_id for item in kept}
        for item in self._store.list_items():
            if tag not in item.tags or item.knowledge_id in current:
                continue
            if item.status in ("ACTIVE", "COLLECTED"):
                self._store.upsert_item(item.model_copy(update={"status": "SUPERSEDED"}))
