"""Artifact registry: manages identity and lineage, never quality."""

from app.models.artifacts import Artifact, ArtifactIndexEntry


class ArtifactRegistry:
    def __init__(self) -> None:
        self._artifacts: dict[str, Artifact] = {}

    def register(self, artifact: Artifact) -> Artifact:
        existing = self._artifacts.get(artifact.artifact_id)
        if existing is not None:
            if self._content(existing) != self._content(artifact):
                raise ValueError(f"Artifact identity {artifact.artifact_id} already exists with different content")
            return existing
        for parent in artifact.lineage:
            if parent not in self._artifacts:
                raise ValueError(f"Unknown lineage parent {parent}")
        self._artifacts[artifact.artifact_id] = artifact
        return artifact

    @staticmethod
    def _content(artifact: Artifact) -> dict:
        """Ignore capture timestamps when deciding whether a re-registration is the same artifact."""
        return artifact.model_dump(exclude={"created_at": True, "provenance": {"retrieved_at"}})

    def get(self, artifact_id: str) -> Artifact:
        try:
            return self._artifacts[artifact_id]
        except KeyError:
            raise KeyError(f"Unknown artifact {artifact_id}") from None

    def __contains__(self, artifact_id: object) -> bool:
        return artifact_id in self._artifacts

    def __len__(self) -> int:
        return len(self._artifacts)

    def artifacts(self) -> tuple[Artifact, ...]:
        return tuple(self._artifacts.values())

    def index(self) -> tuple[ArtifactIndexEntry, ...]:
        """Bounded, payload-free view for the Planner."""
        return tuple(
            ArtifactIndexEntry(
                artifact_ref=artifact.artifact_id,
                artifact_type=artifact.descriptor.artifact_type,
                summary=f"{artifact.descriptor.artifact_type} from {artifact.provenance.source}"
                        + (f", {artifact.row_count} rows" if artifact.row_count is not None else ""),
                row_count=artifact.row_count,
            )
            for artifact in self._artifacts.values()
        )
