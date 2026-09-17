"""Registered export contracts.

``ArtifactExport.value: Any`` alone is not a semantic contract. Each known export type
carries a typed/versioned contract (entity namespace, role, grain, unit, cardinality,
column schema) so downstream consumers can validate compatibility instead of guessing by
convention. This is deliberately a registry of small contracts, not one universal payload
schema.
"""

from __future__ import annotations

from app.models.artifact_runtime import ExportContract, Scope

_CONTRACTS: dict[str, dict] = {
    "PLAYER_ID_SET": {"entity_namespace": "MLBAM", "role": "PLAYER_ID_SET",
                      "grain": "player", "cardinality": "SET"},
    "TEAM_ROSTER": {"entity_namespace": "MLBAM", "role": "ROSTER_TABLE",
                    "grain": "player", "cardinality": "TABLE",
                    "column_schema": (("player_id", "INTEGER"), ("name", "TEXT"),
                                      ("position", "TEXT"))},
    "TEAM_ID": {"entity_namespace": "TEAM_ID", "role": "TEAM_ID", "cardinality": "SCALAR"},
    "DATE_RANGE": {"entity_namespace": "NONE", "role": "DATE_RANGE",
                   "cardinality": "SCALAR"},
    "ENTITY_MAPPING": {"entity_namespace": "MLBAM", "role": "MAPPING",
                       "cardinality": "TABLE"},
    "STATISTICAL_RESULT": {"entity_namespace": "MLBAM", "role": "TABLE",
                           "grain": "entity", "cardinality": "TABLE",
                           "column_schema": (("columns", "TEXT[]"), ("rows", "ANY[]"))},
    "RANKED_ENTITY_SET": {"entity_namespace": "MLBAM", "role": "RANKED_SET",
                          "grain": "entity", "cardinality": "SET"},
    "WEB_EVIDENCE": {"entity_namespace": "NONE", "role": "EVIDENCE",
                     "cardinality": "TABLE"},
    "TRANSACTION_DATE": {"entity_namespace": "NONE", "role": "DATE", "cardinality": "SCALAR"},
    "EVENT_SET": {"entity_namespace": "MLBAM", "role": "EVENT_SET", "grain": "event",
                  "cardinality": "SET"},
    "DERIVED_MEASURE": {"entity_namespace": "NONE", "role": "SCALAR",
                        "cardinality": "SCALAR"},
    "KNOWLEDGE_CANDIDATE": {"entity_namespace": "NONE", "role": "EVIDENCE",
                            "cardinality": "TABLE"},
    "SEARCH_QUERY": {"entity_namespace": "NONE", "role": "QUERY", "cardinality": "SCALAR"},
    "ANOMALY_SIGNAL": {"entity_namespace": "NONE", "role": "SIGNAL", "cardinality": "SCALAR"},
}


def contract_for(export_type: str, *, scope: Scope | None = None) -> ExportContract:
    spec = _CONTRACTS.get(export_type, {})
    return ExportContract(
        schema_name=export_type.lower() or "unknown", version=1,
        entity_namespace=spec.get("entity_namespace", ""),
        role=spec.get("role", export_type), grain=spec.get("grain", ""),
        unit=spec.get("unit", ""), cardinality=spec.get("cardinality", "UNKNOWN"),
        column_schema=tuple(spec.get("column_schema", ())), scope=scope)


def known_export_types() -> tuple[str, ...]:
    return tuple(sorted(_CONTRACTS))


def is_contract_present(export) -> bool:
    return export.contract is not None and bool(export.contract.role)
