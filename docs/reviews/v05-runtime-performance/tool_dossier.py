"""Static ToolRegistry dossier for the v0.5 audit.

Builds the *real* runtime registry and dumps each registered Tool's declared
CapabilityContract plus its implementing class. No invocation and no production change.

Usage:
    python3 docs/reviews/v05-runtime-performance/tool_dossier.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

AUDIT = Path(__file__).resolve().parent
REPO = AUDIT.parents[2]
sys.path.insert(0, str(REPO))

# Static documentation of each Tool's underlying implementation / dependencies and the
# ToolOutcome codes it can emit. Derived by reading the registered implementation.
IMPLEMENTATION_NOTES = {
    "shared_knowledge": {
        "implementation": "app.artifact_runtime.tools_evidence.KnowledgeTool",
        "underlying": "KnowledgeBase.search -> KnowledgeRetriever (SQLite; deterministic token ranking)",
        "external": "none (local SQLite knowledge store)",
        "security": "status/source governance; ACTIVE-only by default",
        "outcome_codes": ["(none=success)", "KNOWLEDGE_UNAVAILABLE", "MISSING_QUERY",
                          "EMPTY_RESULT"],
    },
    "web_research": {
        "implementation": "app.artifact_runtime.tools_evidence.WebResearchTool",
        "underlying": ("app.tools.web_research.WebResearchTool (DuckDuckGo Lite -> Bing; "
                       "PageReader; bounded body, manual redirects)"),
        "external": "public HTTP(S) search + page fetch",
        "security": "SSRF guard (_host_is_safe), http(s) only, 400KB cap, redirect re-check",
        "outcome_codes": ["(none=success)", "WEB_RESEARCH_UNAVAILABLE", "WEB_NO_RESULTS",
                          "MISSING_QUERY", "EMPTY_RESULT"],
    },
    "entity_resolution": {
        "implementation": "app.artifact_runtime.tools_evidence.EntityResolutionTool",
        "underlying": "EntityLookup (local dictionary -> MLB StatsAPI people search -> evidence scan)",
        "external": "statsapi.mlb.com people search (no SSRF guard on this path)",
        "security": "ambiguity preserved; never silently chooses the first candidate",
        "outcome_codes": ["(none=success)", "MISSING_ENTITY_MENTION",
                          "ENTITY_RESOLUTION_UNAVAILABLE", "IDENTITY_AMBIGUOUS",
                          "INPUT_UNRESOLVED"],
    },
    "evidence_entities": {
        "implementation": "app.artifact_runtime.tools_evidence.EvidenceEntityTool",
        "underlying": "EntityLookup scan/resolve over accepted grounded evidence text",
        "external": "statsapi.mlb.com people search (via EntityLookup, English names)",
        "security": "only accepted OK/PARTIAL grounded artifacts; spans recorded",
        "outcome_codes": ["(none=success)", "ENTITY_RESOLUTION_UNAVAILABLE",
                          "INPUT_INCOMPATIBLE", "IDENTITY_AMBIGUOUS", "INPUT_UNRESOLVED"],
    },
    "roster": {
        "implementation": "app.artifact_runtime.tools_evidence.RosterTool",
        "underlying": ("app.artifact_runtime.roster.MLBTeamRosterProvider -> "
                       "statsapi.mlb.com /teams and /teams/{id}/roster?rosterType=active"),
        "external": "statsapi.mlb.com (requests, 12s timeout, no SSRF guard on this path)",
        "security": "current-active roster only; explicit rejection of season/date scope",
        "outcome_codes": ["(none=success)", "MISSING_TEAM", "UNSUPPORTED_CAPABILITY",
                          "ROSTER_UNAVAILABLE", "ROSTER_EMPTY"],
    },
    "batting_stats": {
        "implementation": "app.artifact_runtime.tools_analytics.BattingTool",
        "underlying": "app.tools.batting.BattingStatsTool (pybaseball / Baseball Reference)",
        "external": "Baseball Reference via pybaseball (network)",
        "security": "only provider-supported measures; no silent downgrade to OPS",
        "outcome_codes": ["(none=success)", "BATTING_STATS_TOOL_UNAVAILABLE",
                          "UNSUPPORTED_OPERATION", "BATTING_STATS_UNAVAILABLE",
                          "INVALID_TIME_RANGE", "EMPTY_RESULT"],
    },
    "local_analytics": {
        "implementation": "app.artifact_runtime.tools_analytics.LocalAnalyticsTool",
        "underlying": ("Safe Analytical IR -> SchemaCatalog validation -> deterministic SQL "
                       "compiler -> SQL AST guard -> DuckDB (PARQUET) / PostgreSQL (POSTGRES)"),
        "external": "local Parquet archive; PostgreSQL hot store",
        "security": "closed IR, catalog allowlist, read-only SQL guard, read-only role, "
                    "row cap",
        "outcome_codes": ["(none=success)", "MISSING_ANALYTICAL_QUERY", "INVALID_IR",
                          "UNKNOWN_FIELD", "MISSING_ENTITY_SET", "EXECUTOR_UNAVAILABLE",
                          "SOURCE_QUERY_FAILED/error_code", "EMPTY_RESULT"],
    },
    "compute": {
        "implementation": "app.artifact_runtime.tools_analytics.ComputeTool",
        "underlying": "pure-Python arithmetic over aggregated Artifact exports (no eval/SQL)",
        "external": "none",
        "security": "typed numeric reads only; unknown op -> UNSUPPORTED_OPERATION",
        "outcome_codes": ["(none=success)", "COMPUTE_INPUT_MISSING", "COMPUTE_INPUT_EMPTY",
                          "UNSUPPORTED_COMPUTE_OPERATION", "EMPTY_RESULT"],
    },
}


def main() -> int:
    from app.artifact_runtime.factory import build_runtime
    from app.config import settings
    from app.knowledge.service import KnowledgeBase
    from app.knowledge.store import SqliteKnowledgeStore

    store = SqliteKnowledgeStore(settings.knowledge_store_path)
    runtime = build_runtime(use_llm=False, knowledge=KnowledgeBase(store))
    try:
        tools = []
        for tool in runtime._registry.all():
            contract = tool.contract
            tools.append({
                "name": contract.name,
                "implementation_class": f"{type(tool).__module__}.{type(tool).__name__}",
                "accepts": list(contract.accepts),
                "produces": list(contract.produces),
                "description": contract.description,
                "cost": contract.cost,
                "availability": contract.availability,
                "authority": contract.authority,
                "temporal_modes": list(contract.temporal_modes),
                "population_modes": list(contract.population_modes),
                "game_types": list(contract.game_types),
                "entity_namespace": contract.entity_namespace,
                "supported_measures": list(contract.supported_measures),
                "required_inputs": list(contract.required_inputs),
                **IMPLEMENTATION_NOTES.get(contract.name, {}),
            })
        dossier = {"tools": tools,
                   "tool_count": len(tools),
                   "note": "Registry inspected from the product composition root."}
        out = AUDIT / "tool_registry.json"
        out.write_text(json.dumps(dossier, indent=2, ensure_ascii=False), encoding="utf-8")
        for entry in tools:
            print(f"- {entry['name']}: {entry['accepts']} -> {entry['produces']} "
                  f"[{entry['availability']}/{entry['authority']}]")
        print(f"wrote {out}")
    finally:
        runtime.close()
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
