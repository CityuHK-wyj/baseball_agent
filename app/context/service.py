"""Shared Context: a capability, not a retrieval agent.

A ContextRequest is answered deterministically from reference sources. Failed and
rejected history is excluded by default and always for the Response purpose.
"""

from typing import Literal, Protocol

from pydantic import Field

from app.models.artifacts import ArtifactContract
from app.models.contracts import Name

ContextPurpose = Literal["PLANNER", "RESPONSE"]

# History that must never be projected into a downstream consumer.
_ALWAYS_EXCLUDED = frozenset({"ATTEMPT", "ROUTING_DECISION", "PLANNER_DRAFT", "JUDGE_REASONING",
                              "REJECTED_EVIDENCE", "UNUSED_RAG"})
# Superseded planning artifacts a Response Agent must not read.
_RESPONSE_ONLY_EXCLUDED = frozenset({"PLAN"})


class ContextItem(ArtifactContract):
    item_id: Name
    kind: Name
    title: Name
    content: str = ""
    source: Name
    accepted: bool = True
    entity_ref: str = ""
    freshness_rank: int = Field(default=0, ge=0)
    provenance_ref: str = ""
    scope_run: str = ""


class ContextRequest(ArtifactContract):
    request_id: Name
    purpose: ContextPurpose = "PLANNER"
    query: str = ""
    kinds: tuple[Name, ...] = ()
    entity_refs: tuple[Name, ...] = ()
    max_items: int = Field(default=10, ge=1)
    run_id: str = ""


class ContextPackage(ArtifactContract):
    request_ref: Name
    purpose: ContextPurpose
    items: tuple[ContextItem, ...] = ()
    total_available: int = 0
    truncated: bool = False


class ContextSource(Protocol):
    kind: str

    def retrieve(self, request: ContextRequest) -> tuple[ContextItem, ...]: ...


class StaticContextSource:
    """Reference source over in-memory items; a registry can implement the same seam."""

    def __init__(self, kind: str, items: tuple[ContextItem, ...] = ()) -> None:
        self.kind = kind
        self._items: tuple[ContextItem, ...] = ()
        for item in items:
            self.add(item)

    def add(self, item: ContextItem) -> None:
        if item.kind != self.kind:
            raise ValueError(f"Source {self.kind} cannot hold kind {item.kind}")
        self._items += (item,)

    def retrieve(self, request: ContextRequest) -> tuple[ContextItem, ...]:
        return self._items


def _excluded(kind: str, purpose: ContextPurpose) -> bool:
    if kind in _ALWAYS_EXCLUDED:
        return True
    return purpose == "RESPONSE" and kind in _RESPONSE_ONLY_EXCLUDED


class ContextService:
    def __init__(self, sources: tuple[ContextSource, ...] = ()) -> None:
        self._sources = tuple(sources)

    def register(self, source: ContextSource) -> None:
        self._sources += (source,)

    def retrieve(self, request: ContextRequest) -> ContextPackage:
        collected: dict[str, ContextItem] = {}
        for source in self._sources:
            if request.kinds and source.kind not in request.kinds:
                continue
            for item in source.retrieve(request):
                if _excluded(item.kind, request.purpose) or not item.accepted:
                    continue
                if item.scope_run and item.scope_run != request.run_id:
                    continue
                if request.entity_refs and item.entity_ref and item.entity_ref not in request.entity_refs:
                    continue
                collected.setdefault(item.item_id, item)
        ordered = sorted(collected.values(),
                         key=lambda item: (item.freshness_rank, item.kind, item.item_id))
        selected = tuple(ordered[:request.max_items])
        return ContextPackage(request_ref=request.request_id, purpose=request.purpose,
                              items=selected, total_available=len(ordered),
                              truncated=len(ordered) > len(selected))
