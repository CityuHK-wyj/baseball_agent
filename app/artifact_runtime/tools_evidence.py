"""Evidence-producing tools: Shared Knowledge, Web research, entity resolution, roster.

Every tool emits a normal :class:`RuntimeArtifact` with references and provenance. Web
evidence distinguishes a *search hit* from *retrieved evidence*: a snippet is not
grounded support.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.models.artifact_runtime import (EvidenceSource, RuntimeArtifact, Scope,
                                         ToolCapabilityContract, ToolRequest)
from app.artifact_runtime.roster import RosterUnavailable
from app.artifact_runtime.tool_base import RuntimeTool, ToolContext, ToolOutcome, build_export


def _now() -> datetime:
    return datetime.now(timezone.utc)


class KnowledgeTool(RuntimeTool):
    """Read path over ACTIVE approved Shared Knowledge -> a normal Artifact."""

    contract = ToolCapabilityContract(
        name="shared_knowledge", accepts=("SEARCH_QUERY",),
        produces=("KNOWLEDGE_CANDIDATE",),
        description="Curated baseball rules, terms, teams, players, awards.")

    def run(self, request: ToolRequest, context: ToolContext) -> ToolOutcome:
        if context.knowledge is None:
            return ToolOutcome(recovery_code="KNOWLEDGE_UNAVAILABLE",
                               detail="shared knowledge is not configured")
        query = str(request.structured_inputs.get("query") or request.objective or "").strip()
        if not query:
            return ToolOutcome(recovery_code="MISSING_QUERY", detail="knowledge needs a query")
        try:
            matches = context.knowledge.search(query, max_items=5)
        except Exception as error:  # noqa: BLE001 - retrieval failure is a recovery signal
            return ToolOutcome(recovery_code="KNOWLEDGE_UNAVAILABLE",
                               detail=f"{type(error).__name__}: {error}")
        if not matches:
            artifact = RuntimeArtifact(
                artifact_id=context.artifacts.next_id("knowledge"), kind="knowledge",
                structured_data={"query": query, "matches": []},
                requested_scope=Scope(note=query), actual_scope=Scope(note=query),
                status="EMPTY", confidence=0.0)
            context.artifacts.add(artifact)
            return ToolOutcome(artifacts=(artifact,))
        refs: list[str] = []
        provenance: list[EvidenceSource] = []
        entries: list[dict] = []
        texts: list[str] = []
        entities: list[str] = []
        for match in matches:
            item = match.item
            reference = context.refs.add("KNOWLEDGE_ENTRY", item.knowledge_id,
                                         label=item.title)
            refs.append(reference.ref_id)
            provenance.append(EvidenceSource(
                source="shared-knowledge", source_kind="KNOWLEDGE",
                reference=item.knowledge_id, title=item.title,
                authority=item.source_authority))
            entries.append({"knowledge_id": item.knowledge_id,
                            "knowledge_type": item.knowledge_type,
                            "title": item.title, "summary": item.summary,
                            "authority": item.source_authority,
                            "score": match.score,
                            "reference_ref": reference.ref_id})
            if item.summary:
                texts.append(f"{item.title}: {item.summary}")
            entities.extend(item.entity_refs)
        artifact = RuntimeArtifact(
            artifact_id=context.artifacts.next_id("knowledge"), kind="knowledge",
            structured_data={"query": query, "matches": entries},
            text_content="\n".join(texts)[:6000],
            provenance=tuple(provenance), references=tuple(refs),
            requested_scope=Scope(note=query),
            actual_scope=Scope(entities=tuple(dict.fromkeys(entities)), note=query),
            confidence=0.7, status="OK")
        artifact = artifact.model_copy(update={"exports": (
            build_export(artifact, "KNOWLEDGE_CANDIDATE", entries,
                         text="\n".join(texts)[:2000], references=tuple(refs),
                         provenance="shared-knowledge", confidence=0.7,
                         metadata={"query": query}),)})
        context.artifacts.add(artifact)
        return ToolOutcome(artifacts=(artifact,))


class WebResearchTool(RuntimeTool):
    """Free-form web research. Search hits are separated from grounded page evidence."""

    contract = ToolCapabilityContract(
        name="web_research",
        accepts=("SEARCH_QUERY", "ENTITY_CONTEXT", "STATISTICAL_RESULT", "ANOMALY_SIGNAL"),
        produces=("WEB_EVIDENCE",),
        description="Live web search plus page reading for unknown references and news.",
        cost=2)

    def run(self, request: ToolRequest, context: ToolContext) -> ToolOutcome:
        if context.web is None:
            return ToolOutcome(recovery_code="WEB_RESEARCH_UNAVAILABLE",
                               detail="web research is not configured")
        query = str(request.structured_inputs.get("query") or request.objective or "").strip()
        if not query:
            return ToolOutcome(recovery_code="MISSING_QUERY", detail="web research needs a query")
        try:
            items = context.web.research(query, fetch_pages=2)
        except Exception as error:  # noqa: BLE001 - transport failures are recoverable
            return ToolOutcome(recovery_code="WEB_RESEARCH_UNAVAILABLE",
                               detail=f"{type(error).__name__}: {error}")
        if not items:
            return ToolOutcome(recovery_code="WEB_NO_RESULTS", detail=f"no results for {query!r}")
        findings: list[dict] = []
        provenance: list[EvidenceSource] = []
        refs: list[str] = []
        snippets: list[str] = []
        for item in items:
            data = item.data or {}
            grounded = bool(data.get("fetched")) and bool(item.text)
            span = (item.text or "")[:(1200 if grounded else 0)]
            reference = context.refs.add("WEB_EVIDENCE_SPAN", item.reference,
                                         selector="page" if grounded else "snippet",
                                         label=item.summary)
            refs.append(reference.ref_id)
            provenance.append(EvidenceSource(
                source=item.source, source_kind="WEB", reference=item.reference,
                title=str(data.get("title") or item.summary), url=item.reference,
                retrieved_at=item.retrieved_at))
            findings.append({
                "url": item.reference, "title": data.get("title") or item.summary,
                "snippet": data.get("snippet") or "", "grounded": grounded,
                "evidence_span": span, "reference_ref": reference.ref_id,
                "retrieved_at": item.retrieved_at.isoformat(),
            })
            snippets.append(str(data.get("snippet") or item.text or ""))
        grounded_any = any(finding["grounded"] for finding in findings)
        status = "OK" if grounded_any else "PARTIAL"
        source_artifacts: list[str] = []
        for ref in request.input_refs:
            reference = context.refs.maybe(ref)
            if reference is not None and reference.ref_type in ("ARTIFACT", "ARTIFACT_EXPORT"):
                source_artifacts.append(reference.target_id)
        artifact = RuntimeArtifact(
            artifact_id=context.artifacts.next_id("web"), kind="web_evidence",
            structured_data={"query": query, "findings": findings,
                             "source_artifacts": source_artifacts},
            text_content="\n".join(item.text for item in items)[:6000],
            provenance=tuple(provenance), references=tuple(refs),
            requested_scope=Scope(note=query), actual_scope=None,
            lineage=tuple(source_artifacts),
            confidence=0.6 if grounded_any else 0.25, status=status)
        artifact = artifact.model_copy(update={"exports": (
            build_export(artifact, "WEB_EVIDENCE", findings,
                         text="\n".join(snippets)[:2000], references=tuple(refs),
                         provenance="web-research", confidence=artifact.confidence),)})
        context.artifacts.add(artifact)
        return ToolOutcome(artifacts=(artifact,))


class EntityResolutionTool(RuntimeTool):
    """Resolve mentions to canonical entities / a reusable PLAYER_ID_SET."""

    contract = ToolCapabilityContract(
        name="entity_resolution", accepts=("ENTITY_MENTION",),
        produces=("ENTITY_MAPPING", "PLAYER_ID_SET"),
        description="Resolve unknown references to canonical MLB entities.")

    def run(self, request: ToolRequest, context: ToolContext) -> ToolOutcome:
        mentions = tuple(str(item) for item in request.structured_inputs.get("mentions", ())
                         if str(item).strip())
        if not mentions:
            return ToolOutcome(recovery_code="MISSING_ENTITY_MENTION",
                               detail="entity resolution needs mentions")
        if context.entity_lookup is None:
            return ToolOutcome(recovery_code="ENTITY_RESOLUTION_UNAVAILABLE",
                               detail="entity resolution is not configured")
        mapping: dict[str, str] = {}
        ids: list[int] = []
        refs: list[str] = []
        provenance: list[EvidenceSource] = []
        for mention in mentions:
            result = context.entity_lookup.resolve(mention)
            if result.resolved and result.canonical is not None:
                mapping[mention] = result.canonical.entity_key
                identifier = result.canonical.entity_key.partition(":")[2]
                if identifier.isdigit():
                    ids.append(int(identifier))
                reference = context.refs.add("KNOWLEDGE_ENTRY", result.canonical.entity_key,
                                             label=result.canonical.display_name)
                refs.append(reference.ref_id)
                provenance.append(EvidenceSource(
                    source=result.source or "entity-resolution", source_kind="ENTITY",
                    reference=result.canonical.entity_key,
                    title=result.canonical.display_name))
        status = "OK" if mapping else "EMPTY"
        artifact = RuntimeArtifact(
            artifact_id=context.artifacts.next_id("entities"), kind="entity_mapping",
            structured_data={"mapping": mapping, "mentions": list(mentions)},
            text_content=", ".join(f"{k} -> {v}" for k, v in mapping.items()),
            provenance=tuple(provenance), references=tuple(refs),
            requested_scope=Scope(entities=mentions),
            actual_scope=Scope(entities=tuple(mapping.values())),
            confidence=0.8 if mapping else 0.0, status=status)
        exports = [build_export(artifact, "ENTITY_MAPPING", mapping,
                                references=tuple(refs), provenance="entity-resolution",
                                confidence=artifact.confidence)]
        if ids:
            exports.append(build_export(artifact, "PLAYER_ID_SET", list(dict.fromkeys(ids)),
                                        references=tuple(refs),
                                        provenance="entity-resolution",
                                        confidence=artifact.confidence))
        artifact = artifact.model_copy(update={"exports": tuple(exports)})
        context.artifacts.add(artifact)
        return ToolOutcome(artifacts=(artifact,))


class EvidenceEntityTool(RuntimeTool):
    """Extract canonical player entities from referenced evidence text."""

    contract = ToolCapabilityContract(
        name="evidence_entities", accepts=("WEB_EVIDENCE", "KNOWLEDGE_CANDIDATE"),
        produces=("ENTITY_MAPPING", "PLAYER_ID_SET"),
        description="Extract canonical player entities from referenced evidence text.")

    def run(self, request: ToolRequest, context: ToolContext) -> ToolOutcome:
        if context.entity_lookup is None:
            return ToolOutcome(recovery_code="ENTITY_RESOLUTION_UNAVAILABLE",
                               detail="entity resolution is not configured")
        texts: list[str] = []
        source_ids: list[str] = []
        for ref in request.input_refs:
            reference = context.refs.maybe(ref)
            if reference is not None and reference.ref_type in ("ARTIFACT", "ARTIFACT_EXPORT"):
                artifact = context.artifacts.maybe(reference.target_id)
                if artifact is not None:
                    texts.append(artifact.text_content)
                    texts.append(str(artifact.structured_data))
                    source_ids.append(artifact.artifact_id)
        if not texts:
            return ToolOutcome(recovery_code="MISSING_EVIDENCE_INPUT",
                               detail="evidence_entities needs a referenced evidence artifact")
        found = context.entity_lookup.scan(" ".join(texts))
        mapping = {entity.display_name: entity.entity_key for entity in found}
        ids = [int(entity.entity_key.partition(":")[2]) for entity in found
               if entity.entity_key.partition(":")[2].isdigit()]
        refs = [context.refs.add("KNOWLEDGE_ENTRY", entity.entity_key,
                                 label=entity.display_name).ref_id for entity in found]
        artifact = RuntimeArtifact(
            artifact_id=context.artifacts.next_id("entities"), kind="entity_mapping",
            structured_data={"mapping": mapping, "source_artifacts": source_ids},
            text_content=", ".join(mapping), references=tuple(refs),
            lineage=tuple(source_ids), status="OK" if mapping else "EMPTY",
            confidence=0.6 if mapping else 0.0)
        exports = [build_export(artifact, "ENTITY_MAPPING", mapping,
                                references=tuple(refs), provenance="evidence-entities")]
        if ids:
            exports.append(build_export(artifact, "PLAYER_ID_SET", ids,
                                        references=tuple(refs), provenance="evidence-entities"))
        artifact = artifact.model_copy(update={"exports": tuple(exports)})
        context.artifacts.add(artifact)
        return ToolOutcome(artifacts=(artifact,))


class RosterTool(RuntimeTool):
    """Authoritative team -> roster -> PLAYER_ID_SET. No city-name heuristics."""

    contract = ToolCapabilityContract(
        name="roster", accepts=("TEAM_NAME", "SEARCH_QUERY"),
        produces=("TEAM_ROSTER", "PLAYER_ID_SET"),
        description="Resolve a team to its authoritative roster and canonical player ids.",
        cost=2)

    def run(self, request: ToolRequest, context: ToolContext) -> ToolOutcome:
        team = str(request.structured_inputs.get("team") or request.objective or "").strip()
        if not team:
            return ToolOutcome(recovery_code="MISSING_TEAM", detail="roster needs a team name")
        provider = context.roster_provider
        if provider is None:
            from app.artifact_runtime.roster import MLBTeamRosterProvider
            provider = MLBTeamRosterProvider()
        try:
            players = provider(team)
        except RosterUnavailable as error:
            return ToolOutcome(recovery_code="ROSTER_UNAVAILABLE", detail=str(error))
        except Exception as error:  # noqa: BLE001 - provider/network failure is recoverable
            return ToolOutcome(recovery_code="ROSTER_UNAVAILABLE",
                               detail=f"{type(error).__name__}: {error}")
        if not players:
            return ToolOutcome(recovery_code="ROSTER_EMPTY", detail=f"no roster for {team!r}")
        ids = [int(str(item["player_id"])) for item in players
               if str(item.get("player_id", "")).isdigit()]
        roster = tuple({"player_id": str(item["player_id"]), "name": item.get("name", ""),
                        "position": item.get("position", "")} for item in players)
        team_name = str(players[0].get("team_name") or team)
        artifact = RuntimeArtifact(
            artifact_id=context.artifacts.next_id("roster"), kind="team_roster",
            structured_data={"team": team_name, "players": list(roster)},
            text_content=", ".join(item["name"] for item in roster if item.get("name"))[:4000],
            requested_scope=Scope(entities=(team,), population="players"),
            actual_scope=Scope(entities=(team_name,), population="players"),
            confidence=0.95, status="OK" if ids else "PARTIAL")
        exports = [build_export(artifact, "TEAM_ROSTER", list(roster),
                                provenance="roster-provider", confidence=0.95,
                                metadata={"team": team_name})]
        if ids:
            exports.append(build_export(artifact, "PLAYER_ID_SET", ids,
                                        provenance="roster-provider", confidence=0.95,
                                        metadata={"team": team_name}))
        artifact = artifact.model_copy(update={"exports": tuple(exports)})
        context.artifacts.add(artifact)
        return ToolOutcome(artifacts=(artifact,))
