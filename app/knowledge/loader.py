"""Load committed source manifests and seed packs, and rebuild the knowledge store.

The committed ``knowledge/`` directory plus this code reproduce the store. A store built
this way is a *seed*: live refresh replaces reference domains with freshly fetched data.
"""

import json
from pathlib import Path

from app.knowledge.ingestion import KnowledgePack, KnowledgeIngester, load_pack
from app.models.knowledge import KnowledgeDiff, KnowledgeSource

SOURCE_MANIFESTS = ("official.json", "reference.json", "community.json")
SEED_PACKS = ("reference.json", "rules.json", "glossary.json", "players.json", "community.json")


def load_sources(source_dir: str | Path) -> tuple[KnowledgeSource, ...]:
    directory = Path(source_dir)
    sources: list[KnowledgeSource] = []
    for name in SOURCE_MANIFESTS:
        path = directory / name
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        sources.extend(KnowledgeSource.model_validate(entry) for entry in payload.get("sources", ()))
    seen: set[str] = set()
    for source in sources:
        if source.source_id in seen:
            raise ValueError(f"duplicate source id across manifests: {source.source_id!r}")
        seen.add(source.source_id)
    return tuple(sources)


def load_packs(seed_dir: str | Path) -> tuple[KnowledgePack, ...]:
    directory = Path(seed_dir)
    packs = []
    for name in SEED_PACKS:
        path = directory / name
        if path.exists():
            packs.append(load_pack(path))
    return tuple(packs)


def seed_store(store, source_dir: str | Path, seed_dir: str | Path, *,
               activate: bool = True) -> tuple[KnowledgeDiff, ...]:
    """Rebuild the store from committed manifests + seed packs. Idempotent."""
    for source in load_sources(source_dir):
        store.upsert_source(source)
    ingester = KnowledgeIngester(store)
    return tuple(ingester.ingest(pack, activate=activate) for pack in load_packs(seed_dir))
