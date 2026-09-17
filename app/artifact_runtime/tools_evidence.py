"""Evidence-producing tools: Shared Knowledge, Web research, entity resolution, roster.

Every tool emits a normal :class:`RuntimeArtifact` with references and provenance. Web
evidence distinguishes a *search hit* from *retrieved evidence*: a snippet is not
grounded support.
"""

from __future__ import annotations

from app.models.artifact_runtime import (EvidenceSource, RuntimeArtifact, Scope,
                                         ToolCapabilityContract, ToolRequest)
from app.artifact_runtime.roster import RosterUnavailable
from app.artifact_runtime.tool_base import RuntimeTool, ToolContext, ToolOutcome, build_export


def _bound_value_context(context: ToolContext, request: ToolRequest) -> str:
    """Render a bounded summary of explicitly bound upstream values.

    This is what makes ``DB -> Web`` a real dataflow: the downstream query changes when
    the bound value changes, not merely its lineage.
    """
    from app.artifact_runtime.tool_base import resolve_export_ref
    parts: list[str] = []
    for ref in request.input_refs:
        export = resolve_export_ref(context, ref)
        if export is None:
            continue
        value = export.value
        if isinstance(value, dict):
            if "columns" in value and "rows" in value:
                columns = [str(item) for item in value.get("columns", ())]
                rows = list(value.get("rows", ()))[:3]
                parts.append("; ".join(
                    ", ".join(f"{columns[i]}={cell}" for i, cell in enumerate(row)
                              if i < len(columns)) for row in rows))
            elif "value" in value:
                parts.append(f"{value.get('label', 'measure')}={value['value']}")
            else:
                parts.append(", ".join(f"{key}={val}" for key, val in list(value.items())[:4]))
        elif isinstance(value, (list, tuple)):
            parts.append(", ".join(str(item) for item in list(value)[:5]))
        elif value is not None:
            parts.append(str(value))
    return " | ".join(part for part in parts if part)[:300]


class KnowledgeTool(RuntimeTool):
    """Read path over ACTIVE approved Shared Knowledge -> a normal Artifact.

    Normal authoritative retrieval explicitly excludes HISTORICAL and pending candidates;
    historical/contextual material is only reachable through an explicit as_of/status
    request, never as an unconditional global truth.
    """

    contract = ToolCapabilityContract(
        name="shared_knowledge", accepts=("SEARCH_QUERY",),
        produces=("KNOWLEDGE_CANDIDATE",),
        description="Curated ACTIVE baseball rules, terms, teams, players, awards.",
        temporal_modes=("ARBITRARY",), authority="AUTHORITATIVE")

    def run(self, request: ToolRequest, context: ToolContext) -> ToolOutcome:
        if context.knowledge is None:
            return ToolOutcome(recovery_code="KNOWLEDGE_UNAVAILABLE",
                               detail="shared knowledge is not configured")
        query = str(request.structured_inputs.get("query") or request.objective or "").strip()
        if not query:
            return ToolOutcome(recovery_code="MISSING_QUERY", detail="knowledge needs a query")
        as_of = request.structured_inputs.get("as_of")
        statuses = tuple(request.structured_inputs.get("statuses") or ("ACTIVE",))
        if any(status != "ACTIVE" for status in statuses):
            # Non-ACTIVE/authoritative reads are an explicit, disclosed mode.
            pass
        changes: dict = {"max_items": 5, "statuses": statuses}
        if as_of:
            changes["as_of"] = as_of
        try:
            matches = context.knowledge.search(query, **changes)
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
            structured_data={"query": query, "matches": entries,
                             "statuses": list(statuses)},
            text_content="\n".join(texts)[:6000],
            provenance=tuple(provenance), references=tuple(refs),
            requested_scope=Scope(note=query),
            actual_scope=Scope(entities=tuple(dict.fromkeys(entities)), note=query),
            confidence=0.7, status="OK",
            metadata={"execution_receipt": {
                "source_kind": "KNOWLEDGE", "query": query,
                "statuses": list(statuses), "active_only": statuses == ("ACTIVE",),
                "canonical_entities": list(dict.fromkeys(entities)),
                "source_snapshot": ",".join(item["knowledge_id"] for item in entries[:8]),
                "evidence_refs": list(refs)}})
        artifact = artifact.model_copy(update={"exports": (
            build_export(artifact, "KNOWLEDGE_CANDIDATE", entries,
                         text="\n".join(texts)[:2000], references=tuple(refs),
                         provenance="shared-knowledge", confidence=0.7,
                         metadata={"query": query}),)})
        context.artifacts.add(artifact)
        return ToolOutcome(artifacts=(artifact,))


class WebResearchTool(RuntimeTool):
    """Free-form web research. Search hits are separated from grounded page evidence.

    A fetched page does not automatically make every snippet/body fragment accepted
    evidence: only located, bounded spans from retrieved pages are grounded, and a mixed
    artifact is PARTIAL rather than OK so ungrounded text cannot be laundered into an
    accepted claim.
    """

    contract = ToolCapabilityContract(
        name="web_research",
        accepts=("SEARCH_QUERY", "ENTITY_CONTEXT", "STATISTICAL_RESULT", "ANOMALY_SIGNAL",
                 "DERIVED_MEASURE"),
        produces=("WEB_EVIDENCE",),
        description="Live web search plus page reading for unknown references and news.",
        cost=2, availability="CONFIG_REQUIRED", authority="OBSERVED")

    def run(self, request: ToolRequest, context: ToolContext) -> ToolOutcome:
        if context.web is None:
            return ToolOutcome(recovery_code="WEB_RESEARCH_UNAVAILABLE",
                               detail="web research is not configured")
        query = str(request.structured_inputs.get("query") or request.objective or "").strip()
        if not query:
            return ToolOutcome(recovery_code="MISSING_QUERY", detail="web research needs a query")
        # Explicit bindings may parameterize the query with upstream values, so DB results
        # genuinely change the research rather than only appearing in lineage.
        bound_context = _bound_value_context(context, request)
        effective_query = query + (f" | based on: {bound_context}" if bound_context else "")
        try:
            items = context.web.research(effective_query, fetch_pages=2)
        except Exception as error:  # noqa: BLE001 - transport failures are recoverable
            return ToolOutcome(recovery_code="WEB_RESEARCH_UNAVAILABLE",
                               detail=f"{type(error).__name__}: {error}")
        if not items:
            return ToolOutcome(recovery_code="WEB_NO_RESULTS", detail=f"no results for {query!r}")
        findings: list[dict] = []
        provenance: list[EvidenceSource] = []
        refs: list[str] = []
        grounded_text: list[str] = []
        grounded_spans: list[str] = []
        for item in items:
            data = item.data or {}
            grounded = bool(data.get("fetched")) and bool(item.text)
            span = (item.text or "")[:1200] if grounded else ""
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
                "accepted": grounded, "evidence_span": span,
                "reference_ref": reference.ref_id,
                "retrieved_at": item.retrieved_at.isoformat(),
            })
            if grounded:
                grounded_text.append(item.text or "")
                grounded_spans.append(item.reference)
        grounded_count = sum(1 for finding in findings if finding["grounded"])
        ungrounded_count = len(findings) - grounded_count
        # Mixed provenance is PARTIAL; only an entirely grounded result is OK.
        status = "OK" if grounded_count and not ungrounded_count else (
            "PARTIAL" if grounded_count else "PARTIAL")
        source_artifacts: list[str] = []
        for ref in request.input_refs:
            reference = context.refs.maybe(ref)
            if reference is not None and reference.ref_type in ("ARTIFACT", "ARTIFACT_EXPORT"):
                source_artifacts.append(reference.target_id)
        artifact = RuntimeArtifact(
            artifact_id=context.artifacts.next_id("web"), kind="web_evidence",
            structured_data={"query": effective_query, "findings": findings,
                             "source_artifacts": source_artifacts},
            # Only grounded spans flow into text_content, so ungrounded snippets cannot be
            # quoted as support by the claim builder.
            text_content="\n".join(grounded_text)[:6000],
            provenance=tuple(provenance), references=tuple(refs),
            requested_scope=Scope(note=query), actual_scope=None,
            lineage=tuple(dict.fromkeys(source_artifacts)),
            confidence=0.6 if grounded_count and not ungrounded_count else 0.25,
            status=status,
            metadata={"execution_receipt": {
                "grounded_count": grounded_count, "ungrounded_count": ungrounded_count,
                "fetched_spans": grounded_spans, "query": effective_query,
                "canonical_entities": [], "evidence_refs": list(refs),
                "source_snapshot": ",".join(grounded_spans[:8])}})
        artifact = artifact.model_copy(update={"exports": (
            build_export(artifact, "WEB_EVIDENCE", findings,
                         text="\n".join(grounded_text)[:2000], references=tuple(refs),
                         provenance="web-research", confidence=artifact.confidence),)})
        context.artifacts.add(artifact)
        return ToolOutcome(artifacts=(artifact,))


class EntityResolutionTool(RuntimeTool):
    """Resolve mentions to canonical entities / a reusable PLAYER_ID_SET."""

    contract = ToolCapabilityContract(
        name="entity_resolution", accepts=("ENTITY_MENTION",),
        produces=("ENTITY_MAPPING", "PLAYER_ID_SET"),
        description="Resolve unknown references to canonical MLB entities.",
        entity_namespace="MLBAM", authority="DERIVED")

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
            confidence=0.8 if mapping else 0.0, status=status,
            metadata={"execution_receipt": {
                "canonical_entities": list(mapping.values()),
                "resolver": "entity-lookup", "evidence_refs": list(refs),
                "source_snapshot": ",".join(list(mapping.values())[:8])}})
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
    """Extract canonical player entities from referenced evidence text.

    It may only operate on *accepted* (grounded) upstream evidence; it must not launder
    ungrounded snippets or rejected products into an accepted entity set.
    """

    contract = ToolCapabilityContract(
        name="evidence_entities", accepts=("WEB_EVIDENCE", "KNOWLEDGE_CANDIDATE"),
        produces=("ENTITY_MAPPING", "PLAYER_ID_SET"),
        description="Extract canonical player entities from referenced evidence text.",
        entity_namespace="MLBAM", authority="DERIVED")

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
                if artifact is None:
                    continue
                if artifact.status not in ("OK", "PARTIAL"):
                    # A rejected/invalid artifact cannot be laundered into a trusted set.
                    continue
                # Only grounded text is eligible; ungrounded snippets in structured_data
                # are explicitly excluded from the scan.
                texts.append(artifact.text_content)
                source_ids.append(artifact.artifact_id)
        if not texts:
            return ToolOutcome(recovery_code="INPUT_INCOMPATIBLE",
                               detail="evidence_entities needs an accepted grounded "
                                      "evidence artifact")
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
            confidence=0.6 if mapping else 0.0,
            metadata={"execution_receipt": {
                "canonical_entities": list(mapping.values()),
                "resolver": "evidence-entities", "evidence_refs": list(refs),
                "source_snapshot": ",".join(source_ids)}})
        exports = [build_export(artifact, "ENTITY_MAPPING", mapping,
                                references=tuple(refs), provenance="evidence-entities")]
        if ids:
            exports.append(build_export(artifact, "PLAYER_ID_SET", ids,
                                        references=tuple(refs), provenance="evidence-entities"))
        artifact = artifact.model_copy(update={"exports": tuple(exports)})
        context.artifacts.add(artifact)
        return ToolOutcome(artifacts=(artifact,))


class RosterTool(RuntimeTool):
    """Team -> CURRENT active roster -> PLAYER_ID_SET. No city-name heuristics.

    Truthful capability: this adapter can only return the provider's *current active*
    roster. It cannot select a season, date range, postseason roster or historical
    membership, and it says so rather than echoing the request.
    """

    contract = ToolCapabilityContract(
        name="roster", accepts=("TEAM_NAME", "SEARCH_QUERY"),
        produces=("TEAM_ROSTER", "PLAYER_ID_SET"),
        description="Team -> current active roster and canonical player ids (no history).",
        cost=2, temporal_modes=("CURRENT_ONLY",),
        population_modes=("active_roster",), entity_namespace="MLBAM",
        authority="AUTHORITATIVE_ACTIVE_ONLY")

    def run(self, request: ToolRequest, context: ToolContext) -> ToolOutcome:
        team = str(request.structured_inputs.get("team") or request.objective or "").strip()
        if not team:
            return ToolOutcome(recovery_code="MISSING_TEAM", detail="roster needs a team name")
        # Defensive rejection of unsupported explicit scope: never silently ignore it.
        if request.structured_inputs.get("season") or request.structured_inputs.get("start"):
            return ToolOutcome(
                recovery_code="UNSUPPORTED_CAPABILITY",
                detail="this roster provider cannot select a historical season/date; "
                       "current active roster only")
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
            structured_data={"team": team_name, "players": list(roster),
                             "roster_type": "active"},
            text_content=", ".join(item["name"] for item in roster if item.get("name"))[:4000],
            requested_scope=Scope(entities=(team,), population="players"),
            actual_scope=Scope(entities=(team_name,), population="players",
                               membership_basis="active_roster"),
            confidence=0.95, status="OK" if ids else "PARTIAL",
            metadata={"execution_receipt": {
                "team": team_name, "roster_type": "active", "population": "players",
                "membership_basis": "active_roster", "provider": "mlb-team-roster",
                "source_snapshot": f"mlb:roster:{team_name}:active",
                "player_count": len(ids)}})
        exports = [build_export(artifact, "TEAM_ROSTER", list(roster),
                                provenance="roster-provider", confidence=0.95,
                                metadata={"team": team_name,
                                          "membership_basis": "active_roster"})]
        if ids:
            exports.append(build_export(artifact, "PLAYER_ID_SET", ids,
                                        provenance="roster-provider", confidence=0.95,
                                        metadata={"team": team_name,
                                                  "membership_basis": "active_roster"}))
        artifact = artifact.model_copy(update={"exports": tuple(exports)})
        context.artifacts.add(artifact)
        return ToolOutcome(artifacts=(artifact,))
