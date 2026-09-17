"""Reference store: stable pointers to original information.

References let the runtime build new interpretations *on top of* original evidence
instead of repeatedly summarizing summaries. Resolving a reference never executes
anything; it only returns the referenced object or export.
"""

from __future__ import annotations

from app.models.artifact_runtime import Reference, RefType, RuntimeArtifact, utcnow


class UnknownReference(KeyError):
    pass


class ReferenceStore:
    """In-memory reference graph for one run/conversation."""

    def __init__(self) -> None:
        self._references: dict[str, Reference] = {}
        self._counter = 0

    def add(self, ref_type: RefType, target_id: str, *, selector: str = "",
            provenance: str = "", label: str = "", ref_id: str | None = None) -> Reference:
        if ref_id is None:
            # Durable identity: never overwrite an existing reference id.
            while True:
                self._counter += 1
                ref_id = f"ref-{self._counter}"
                if ref_id not in self._references:
                    break
        reference = Reference(ref_id=ref_id, ref_type=ref_type, target_id=target_id,
                              selector=selector, provenance=provenance, label=label)
        self._references[ref_id] = reference
        self._bump(ref_id)
        return reference

    def put(self, reference: Reference) -> Reference:
        self._references[reference.ref_id] = reference
        self._bump(reference.ref_id)
        return reference

    def _bump(self, ref_id: str) -> None:
        suffix = ref_id.rsplit("-", 1)[-1]
        if suffix.isdigit():
            self._counter = max(self._counter, int(suffix))

    def get(self, ref_id: str) -> Reference:
        try:
            return self._references[ref_id]
        except KeyError:
            raise UnknownReference(ref_id) from None

    def maybe(self, ref_id: str) -> Reference | None:
        return self._references.get(ref_id)

    def all(self) -> tuple[Reference, ...]:
        return tuple(self._references.values())

    def of_type(self, *ref_types: str) -> tuple[Reference, ...]:
        wanted = set(ref_types)
        return tuple(item for item in self._references.values() if item.ref_type in wanted)

    def __len__(self) -> int:
        return len(self._references)


def artifact_reference(ref_id: str, artifact: RuntimeArtifact, *, label: str = "") -> Reference:
    return Reference(ref_id=ref_id, ref_type="ARTIFACT", target_id=artifact.artifact_id,
                     provenance="artifact-store", label=label or artifact.kind)


def export_reference(ref_id: str, artifact: RuntimeArtifact, export_id: str,
                     *, label: str = "") -> Reference:
    return Reference(ref_id=ref_id, ref_type="ARTIFACT_EXPORT",
                     target_id=artifact.artifact_id, selector=export_id, label=label)


def user_message_reference(conversation_id: str, message_index: int) -> Reference:
    return Reference(ref_id=f"user-{conversation_id}-{message_index}", ref_type="USER_MESSAGE",
                     target_id=f"{conversation_id}:{message_index}", provenance="conversation",
                     label="user message")


def utcnow_iso() -> str:
    return utcnow().isoformat()
