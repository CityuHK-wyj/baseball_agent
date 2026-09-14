"""Artifact payload storage.

Large payloads live in a replaceable storage backend. The operational database
keeps only metadata and a location reference. The first implementation is the
local filesystem; S3/MinIO can implement the same Protocol later.
"""

import hashlib
import re
from pathlib import Path
from typing import Protocol

from app.models.artifacts import ArtifactContract
from app.models.contracts import Name

# A safe artifact reference is a single path segment made of conservative characters.
_SAFE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class StoredArtifact(ArtifactContract):
    artifact_ref: Name
    location: str
    sha256: str
    byte_size: int
    content_type: str = "application/octet-stream"


class ArtifactStorage(Protocol):
    def put(self, artifact_ref: str, payload: bytes,
            content_type: str = "application/octet-stream") -> StoredArtifact: ...

    def get(self, target: "str | StoredArtifact") -> bytes: ...

    def exists(self, target: "str | StoredArtifact") -> bool: ...


class LocalFilesystemArtifactStorage:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def _path(self, reference: str) -> Path:
        if not isinstance(reference, str) or not _SAFE_REFERENCE.match(reference):
            raise ValueError(f"Unsafe artifact reference: {reference!r}")
        path = (self._root / reference).resolve()
        if path.parent != self._root:
            raise ValueError(f"Artifact reference escapes the storage root: {reference!r}")
        return path

    @staticmethod
    def _reference(target: "str | StoredArtifact") -> str:
        return target.artifact_ref if isinstance(target, StoredArtifact) else target

    def put(self, artifact_ref: str, payload: bytes,
            content_type: str = "application/octet-stream") -> StoredArtifact:
        path = self._path(artifact_ref)
        digest = hashlib.sha256(payload).hexdigest()
        if path.exists():
            if path.read_bytes() != payload:
                raise ValueError(f"Artifact {artifact_ref!r} is immutable and already has different content")
        else:
            path.write_bytes(payload)
        return StoredArtifact(artifact_ref=artifact_ref, location=str(path), sha256=digest,
                              byte_size=len(payload), content_type=content_type)

    def get(self, target: "str | StoredArtifact") -> bytes:
        path = self._path(self._reference(target))
        if not path.exists():
            raise FileNotFoundError(f"Artifact {self._reference(target)!r} is not stored")
        return path.read_bytes()

    def exists(self, target: "str | StoredArtifact") -> bool:
        try:
            return self._path(self._reference(target)).exists()
        except ValueError:
            return False

    def delete(self, target: "str | StoredArtifact") -> None:
        path = self._path(self._reference(target))
        if path.exists():
            path.unlink()
