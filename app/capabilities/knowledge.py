"""shared_knowledge: bounded read over ACTIVE knowledge. Produces a Knowledge Artifact."""

from __future__ import annotations

from app.capabilities.base import CapabilityResult, CapabilitySpec
from app.contracts.models import Artifact, Export, Reference, ReferenceType, Scope, ToolOutcome, ToolRequest


class SharedKnowledgeTool:
    def __init__(self, store):
        self.store = store
        self.spec = CapabilitySpec(
            name="shared_knowledge",
            description="Read curated ACTIVE baseball knowledge (entities, definitions, rules).",
            accepts=["SEARCH_QUERY"],
            produces=["KNOWLEDGE_ITEM"],
            required_inputs=[],
        )

    def execute(self, request: ToolRequest, ctx=None) -> CapabilityResult:
        query = str(request.inputs.get("query") or request.objective or "").strip()
        if not query:
            return CapabilityResult(
                None,
                ToolOutcome(status="FAILED", code="INVALID_IR", message="shared_knowledge needs a query", need_id=request.need_id, tool_request_id=request.id, classification="INTERNAL_FAILURE"),
            )
        exact = self.store.match_alias(query)
        items = [exact] if exact else []
        items.extend(item for item in self.store.search(query, limit=5) if item not in items)
        if not items:
            return CapabilityResult(
                None,
                ToolOutcome(status="NO_DATA", code="NO_RESULTS", message=f"no ACTIVE knowledge matched '{query}'", need_id=request.need_id, tool_request_id=request.id, classification="INSUFFICIENT_EVIDENCE"),
            )
        artifact = Artifact(
            kind="KNOWLEDGE_ITEM",
            structured_data={"items": [item.to_dict() for item in items]},
            text_content="\n".join(f"{item.title}: {item.summary}" for item in items),
            exports=[
                Export(
                    id=f"exp_{request.id}_knowledge",
                    type="KNOWLEDGE_ITEM",
                    payload={"items": [item.to_dict() for item in items]},
                    contract={"authority": items[0].authority, "status": "ACTIVE"},
                )
            ],
            provenance={"capability": "shared_knowledge", "request_id": request.id, "items": len(items)},
            references=[Reference(ref_type=ReferenceType.KNOWLEDGE_ITEM, ref_id=item.id, source="knowledge", span=item.summary) for item in items],
            requested_scope=Scope(measure=query),
            actual_scope=Scope(measure=query, source="SHARED_KNOWLEDGE"),
            confidence="STRONG",
            status="ACCEPTED",
            need_id=request.need_id,
        )
        return CapabilityResult(artifact, ToolOutcome(status="SUCCESS", code="OK", message=f"{len(items)} knowledge item(s)", need_id=request.need_id, tool_request_id=request.id))
