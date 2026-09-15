"""Knowledge Source Registry.

A source is described once, with an explicit authority level, so a Reddit thread and the
Official Baseball Rules are never treated as equivalent evidence. The registry is also the
specialisation map the Router may consult later (which source is best for what), without
letting the registry become routing logic.
"""

from app.models.knowledge import AUTHORITY_RANK, AuthorityLevel, KnowledgeSource


class SourceRegistry:
    def __init__(self, sources: tuple[KnowledgeSource, ...] = ()) -> None:
        self._sources: dict[str, KnowledgeSource] = {}
        for source in sources:
            self.add(source)

    def add(self, source: KnowledgeSource) -> None:
        if source.source_id in self._sources:
            raise ValueError(f"Duplicate source id {source.source_id!r}")
        if source.root_url and not source.root_url.startswith(("http://", "https://")):
            raise ValueError(f"Source {source.source_id!r} has an invalid URL")
        self._sources[source.source_id] = source

    def get(self, source_id: str) -> KnowledgeSource:
        try:
            return self._sources[source_id]
        except KeyError:
            raise KeyError(f"Unknown source {source_id!r}") from None

    def sources(self) -> tuple[KnowledgeSource, ...]:
        return tuple(sorted(self._sources.values(), key=lambda item: item.source_id))

    def by_authority(self, authority: AuthorityLevel) -> tuple[KnowledgeSource, ...]:
        return tuple(item for item in self.sources() if item.authority_level == authority)

    def best_for(self, topic: str) -> tuple[KnowledgeSource, ...]:
        needle = topic.casefold()
        matches = [item for item in self.sources() if any(needle == entry.casefold() for entry in item.best_for)]
        return tuple(sorted(matches, key=lambda item: (-AUTHORITY_RANK[item.authority_level], item.source_id)))

    def authority_for(self, source_ids: tuple[str, ...]) -> AuthorityLevel:
        """Highest authority among the referenced sources; UNVERIFIED if none resolve."""
        best: AuthorityLevel = "UNVERIFIED"
        for source_id in source_ids:
            source = self._sources.get(source_id)
            if source is None:
                continue
            if AUTHORITY_RANK[source.authority_level] > AUTHORITY_RANK[best]:
                best = source.authority_level
        return best

    @classmethod
    def from_store(cls, store) -> "SourceRegistry":
        return cls(store.list_sources())
