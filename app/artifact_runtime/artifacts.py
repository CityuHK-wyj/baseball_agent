"""Artifact store: reusable tool outputs with lineage.

Any useful tool output becomes a :class:`RuntimeArtifact`. Another tool consumes it by
reference (an ``ARTIFACT_EXPORT`` reference), so tool direction is never hard-coded.
The store records lineage so an important final claim can be traced back through
derived artifacts to original evidence.
"""

from __future__ import annotations

from app.models.artifact_runtime import ArtifactExport, RuntimeArtifact


class UnknownArtifact(KeyError):
    pass


class ArtifactStore:
    def __init__(self) -> None:
        self._artifacts: dict[str, RuntimeArtifact] = {}
        self._counter = 0

    def next_id(self, prefix: str) -> str:
        # Durable identity: never reuse an id, even after a restore whose counter was
        # not persisted correctly.
        while True:
            self._counter += 1
            candidate = f"{prefix}-{self._counter}"
            if candidate not in self._artifacts:
                return candidate

    def seed_from(self, artifacts: tuple[RuntimeArtifact, ...]) -> None:
        """Restore identity without reusing an existing id."""
        for artifact in artifacts:
            self._artifacts[artifact.artifact_id] = artifact
            self._bump(artifact.artifact_id)

    def _bump(self, artifact_id: str) -> None:
        suffix = artifact_id.rsplit("-", 1)[-1]
        if suffix.isdigit():
            self._counter = max(self._counter, int(suffix))

    def add(self, artifact: RuntimeArtifact) -> RuntimeArtifact:
        if artifact.artifact_id in self._artifacts:
            raise ValueError(f"duplicate artifact id {artifact.artifact_id!r}")
        self._artifacts[artifact.artifact_id] = artifact
        self._bump(artifact.artifact_id)
        return artifact

    def get(self, artifact_id: str) -> RuntimeArtifact:
        try:
            return self._artifacts[artifact_id]
        except KeyError:
            raise UnknownArtifact(artifact_id) from None

    def maybe(self, artifact_id: str) -> RuntimeArtifact | None:
        return self._artifacts.get(artifact_id)

    def update(self, artifact: RuntimeArtifact) -> RuntimeArtifact:
        """Replace a stored artifact (same immutable id) with a verified copy."""
        self._artifacts[artifact.artifact_id] = artifact
        self._bump(artifact.artifact_id)
        return artifact

    def all(self) -> tuple[RuntimeArtifact, ...]:
        return tuple(self._artifacts.values())

    def resolve_export(self, export_id: str) -> ArtifactExport | None:
        for artifact in self._artifacts.values():
            for item in artifact.exports:
                if item.export_id == export_id:
                    return item
        return None

    def exports_of(self, *export_types: str) -> tuple[ArtifactExport, ...]:
        wanted = set(export_types)
        return tuple(item for artifact in self._artifacts.values()
                     for item in artifact.exports if item.export_type in wanted)

    def lineage(self, artifact_id: str) -> tuple[str, ...]:
        """Full upstream artifact id list, nearest first, cycle-safe."""
        seen: list[str] = []
        frontier = [artifact_id]
        while frontier:
            current = frontier.pop(0)
            if current in seen:
                continue
            seen.append(current)
            artifact = self._artifacts.get(current)
            if artifact is not None:
                frontier.extend(parent for parent in artifact.lineage if parent not in seen)
        return tuple(seen)

    def __len__(self) -> int:
        return len(self._artifacts)
