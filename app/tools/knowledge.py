"""Evidence from stored web reference knowledge, using the existing tool contract."""

from uuid import uuid4

from app.agent.executor import ToolResult
from app.models.artifacts import Artifact, Provenance
from app.models.knowledge import KnowledgeQuery
from app.context.knowledge_source import query_date


class KnowledgeTool:
    name = "shared-knowledge"

    def __init__(self, knowledge, requirements):
        self._knowledge = knowledge
        self._requirements = {item.requirement_id: item for item in requirements}

    def execute(self, task):
        requirement = self._requirements[task.requirement_refs[0]]
        if requirement.descriptor.data_keys != ("knowledge_statement",):
            return ToolResult.no_data()
        matches = self._knowledge.retriever.retrieve(KnowledgeQuery(
            query=requirement.description, as_of=query_date(requirement.description),
            authority_floor="AUTHORITATIVE_REFERENCE", max_items=5))
        if not matches:
            return ToolResult.no_data()
        identifier = f"knowledge-{uuid4().hex}"
        artifact = Artifact(artifact_id=identifier, descriptor=requirement.descriptor,
            payload_ref=f"knowledge://{identifier}", row_count=len(matches),
            provenance=Provenance(source="shared-knowledge", source_kind="WEB",
                reference=",".join(item.item.knowledge_id for item in matches)))
        import json
        payload = json.dumps([item.item.model_dump(mode="json") for item in matches]).encode()
        return ToolResult.ok(len(matches), artifact=artifact, payload=payload)
