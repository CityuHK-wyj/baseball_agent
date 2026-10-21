"""Planner: build and repair a Need graph from open objectives.

The Planner decides (a) what information is missing, (b) which capability class to use,
(c) dependencies, (d) desired output. It never emits physical tables/fields, Safe IR,
SQL, Artifact ids or completion verdicts.
"""

from __future__ import annotations

import json
from typing import Any

from app.contracts.models import Goal, NeedCriticality, NeedStatus, PlannerNeed, Reference

SYSTEM_PROMPT = """You are the Planner of a Baseball Agent.

You receive the user's Goal, their Requirements, the current Need graph, the Artifacts that
already exist, and a truthful list of capabilities.

Your job: decide what information is still missing and which capability should produce it.
You build a small Need graph.

The capabilities are grouped into two domain families plus supporting infrastructure.
- LOCAL_DATA_ANALYTICS: retrieval and aggregation over the local read-only source data.
- WEB_EXTERNAL_RESEARCH: open web prose plus structured external records as internal
  adapters (for example current team/roster/status records).
- SUPPORTING: identity resolution, Shared Knowledge reads, projection and bounded
  cross-artifact computation. These are execution infrastructure, not peer sources.
Choose the family that can satisfy the missing evidence; the runtime selects the exact
typed adapter. Do not invent a new tool or fetch a provider directly.

ABSOLUTE RULES
- Never mention a database table name, column name, SQL, IR, or an Artifact/Export id.
- Use only capability names from the provided list.
- `depends_on` may only reference need ids from the provided list or ids you define here.
- Do not put a player id, date or season inside the objective as physical data; put semantic
  scope in `constraints` instead (keys: season, game_population, entity_role, date_range,
  measure, qualification). Values must be plain data, not SQL.
- Do NOT re-create a Need whose desired information already exists in an accepted Artifact.
- A supporting investigation (e.g. news) must be marked "criticality_hint": "supporting".
  A Need that directly serves a user requirement is "criticality_hint": "core".
- `serves` lists the user requirement ids this Need helps satisfy (may be empty).
- Keep the graph minimal. Prefer one analytics Need per distinct measure/role.
- reporting_contract cells are requested original observations; operation_contract operands
  are internal inputs, not extra user reporting obligations. Acquire every needed scalar
  scope with explicit constraints; use separate Needs for distinct time/subject scopes.
  The same complete input can serve both an explicit report and a computation.
  For named scalar cells, set constraints.subjects to the exact subject name(s) from
  that owned reporting cell / operation operand. This only selects an already requested
  scope, never creates one; use one individual subject per Need. A bound identity export
  may contain several players and is not itself a request to pool their statistics.
  Ordinary PER_SUBJECT performance lines can use one grouped analytics Need; do not
  invent a python_compute combination merely to display separately identified rows.
- evidence_intent belongs to each Requirement, not to a tool. PREDICTION requests an
  uncertain analytical outlook or attributed forecast, not a historical fact. Historical
  records can be premises but cannot by themselves establish a prediction. Preserve
  different owner roles; do not replace a requested outlook with confirmed-state retrieval.

HYPOTHESES AND SOURCES
- The `hypotheses` field contains candidate explanations/entities from the model prior or the
  user. They are NOT verified. Use them to choose what to investigate; never treat them as
  evidence and never raise a requirement to satisfied on their basis.
- The `entity_references` field carries the resolved identity of carried/contextual subjects
  (surface form plus canonical id/name/status). Use it directly; never reinterpret a
  pronoun as a new subject.
- Candidate entity names (not canonical ids) may be used with `web_research` and
  `shared_knowledge`. Only a canonical id may be used with `local_analytics` or
  `dynamic_fact`; their input contract is enforced by the runtime, not by you.
- A local database not covering a metric does NOT forbid investigating another source. If a
  database Need fails its entity or measure contract, propose a different permitted path
  (e.g. `web_research`) when it could still satisfy the requirement.
- Failure feedback is structured. Never repeat the same request that just failed; change the
  capability, the constraints, or the source.

OUTPUT JSON
{"needs":[
  {"id":"n1","objective":"...","capability":"entity_resolution","depends_on":[],
   "input_requirements":[],"desired_output":"PLAYER_ID_SET","required_evidence":"canonical identity",
   "constraints":{},"criticality_hint":"core","serves":["req_1"]}
],"note":"short summary"}

CAPABILITY INPUT RULE
- If a capability requires an input (requires_inputs), you MUST list that export type in
  `input_requirements` and the producing need in `depends_on`.
- To compare or combine results from two different seasons/sources, create one analytics
  Need per season/source and a `python_compute` Need that depends_on BOTH of them with
  `input_requirements` listing the export types it consumes. Do not try to span two data
  sources inside a single analytics Need.

CONVERSION AND COMPLETENESS
- A ranked analytical result (`RANKED_ENTITY_SET`) is not itself a web search subject or a
  canonical entity set. To investigate the selected/top entity, add an `artifact_projection`
  Need that depends_on the analytics Need. It projects one explicit row into a
  `SEARCH_QUERY` (and, when a canonical id exists, `PLAYER_ID_SET`) with lineage. Never ask
  a tool to consume `RANKED_ENTITY_SET` directly when it declares another input type.
- For a global comparison, ranking, difference or extremum across artifacts, add a
  `python_compute` Need that depends_on the inputs and set `constraints.global_comparison`
  to true. The runtime compiles an intermediate analytics Need that feeds a
  `python_compute` Need as a complete qualified population, not a pre-truncated top-N.
"""


class Planner:
    def __init__(self, llm, registry, catalog):
        self.llm = llm
        self.registry = registry
        self.catalog = catalog

    @property
    def available(self) -> bool:
        return bool(self.llm and self.llm.configured)

    def plan(
        self,
        *,
        goal: Goal,
        semantic,
        artifacts: list,
        needs: list[PlannerNeed],
        outcomes: list,
        feedback: str = "",
        availability: dict[str, bool] | None = None,
        round_index: int = 0,
        max_needs: int = 8,
    ) -> tuple[list[PlannerNeed], str, list[dict[str, Any]]]:
        if not self.available:
            return self._fallback(goal, semantic), "deterministic fallback plan", []
        payload = {
            "goal": {"id": goal.id, "statement": goal.statement, "raw_query": goal.raw_query},
            "requirements": [
                {"id": r.id, "kind": r.kind.value, "statement": r.statement, "required": r.required,
                 "value": r.value,
                 "evidence_intent": r.evidence_intent.model_dump() if r.evidence_intent else None,
                 "reporting_contract": r.reporting_contract.model_dump() if r.reporting_contract else None,
                 "operation_contract": r.operation_contract.model_dump() if r.operation_contract else None,
                 # Capability-agnostic routing signal (not a hard tool rule); the Planner keeps
                 # its own authority to choose the evidence mechanism.
                 "relation_kind": (r.relation_semantics.relation_kind.value if r.relation_semantics else None),
                 "original_intent": r.original_intent}
                for r in goal.requirements
            ],
            "analytical_resolution": {
                'attempts': goal.analytical_resolution_state.get('attempts', []),
                'clauses': list(goal.analytical_resolution_state.get('current', {}).values()),
                'duplicates_suppressed': goal.analytical_resolution_state.get('duplicates_suppressed', 0),
                'retry_requires': 'new authoritative clarification, identity, or capability context',
            },
            "semantic_understanding": semantic.understanding,
            "entities": [e.model_dump() for e in semantic.entities],
            "entity_references": [
                reference.model_dump(exclude={"reference"})
                for reference in getattr(semantic, "entity_references", [])
            ],
            "hypotheses": [h.model_dump() for h in getattr(semantic, "hypotheses", [])],
            "analysis_requests": [a.model_dump() for a in semantic.analysis_requests],
            "evidence_requests": [e.model_dump() for e in semantic.evidence_requests],
            "constraints": [c.model_dump() for c in semantic.constraints],
            "population_hints": semantic.population_hints,
            "data_affordances": self.catalog.planner_affordances(),
            "capability_families": self.registry.capability_families(),
            "capabilities": self.registry.planner_view(availability),
            "existing_needs": [
                {"id": n.id, "objective": n.objective, "capability": n.capability, "status": n.status.value,
                 "depends_on": n.depends_on}
                for n in needs
            ],
            "accepted_artifacts": [
                {"id": a.id, "kind": a.kind, "need_id": a.need_id, "status": a.status,
                 "exports": [{"type": e.type, "contract": e.contract} for e in a.exports],
                 "scope": (a.actual_scope.model_dump() if a.actual_scope else {}),
                 "summary": (a.text_content or "")[:240]}
                for a in artifacts
            ],
            "attempt_outcomes": [
                {"need_id": o.need_id, "status": o.status, "code": o.code, "message": o.message,
                 "retryable": o.retryable, "classification": o.classification}
                for o in outcomes
            ],
            "round": round_index,
        }
        user = json.dumps(payload, ensure_ascii=False, default=str)
        if feedback:
            user += f"\n\nFEEDBACK FROM THE PREVIOUS ROUND:\n{feedback}"
        try:
            result = self.llm.complete_json(role="planner", system=SYSTEM_PROMPT, user=user, max_tokens=1600)
        except Exception as exc:  # noqa: BLE001
            return [], f"planner call failed: {type(exc).__name__}", []

        raw_needs = result.data.get("needs", [])
        accepted, rejected = self._validate(raw_needs, goal, needs, max_needs)
        note = str(result.data.get("note", ""))[:400]
        return accepted, note, rejected

    # ------------------------------------------------------------------ validation
    def _validate(self, raw_needs, goal: Goal, existing: list[PlannerNeed], max_needs: int):
        accepted: list[PlannerNeed] = []
        rejected: list[dict[str, Any]] = []
        known_ids = {n.id for n in existing}
        capability_names = set(self.registry.names())
        requirement_ids = {r.id for r in goal.requirements}
        seen_new: set[str] = set()

        for index, raw in enumerate(raw_needs if isinstance(raw_needs, list) else []):
            need_id = str(raw.get("id") or f"n{index + 1}")
            capability = str(raw.get("capability") or "").strip()
            if capability not in capability_names:
                rejected.append({"id": need_id, "reason": "UNSUPPORTED_CAPABILITY", "detail": capability})
                continue
            if need_id in seen_new or need_id in known_ids:
                rejected.append({"id": need_id, "reason": "DUPLICATE_NEED_ID", "detail": capability})
                continue
            constraints = dict(raw.get("constraints") or {})
            physical = [key for key in constraints if key.lower() in {"table", "field", "columns", "sql", "ir", "group_by", "aggregate", "source_relation"}]
            if physical:
                for key in physical:
                    constraints.pop(key, None)
                rejected.append({"id": need_id, "reason": "PHYSICAL_SCHEMA_STRIPPED", "detail": ", ".join(physical)})
            depends_on = [str(d) for d in raw.get("depends_on", []) if str(d)]
            unknown_deps = [d for d in depends_on if d not in known_ids and d not in seen_new and d != need_id]
            if unknown_deps:
                rejected.append({"id": need_id, "reason": "DEPENDENCY_REJECTED", "detail": ", ".join(unknown_deps)})
                continue
            # Detect pseudo references in declared inputs.
            pseudo = [r for r in raw.get("input_refs", []) if isinstance(r, str) and ":" in r]
            if pseudo:
                rejected.append({"id": need_id, "reason": "PSEUDO_REFERENCE", "detail": ", ".join(pseudo)})
            serves = [s for s in raw.get("serves", []) if s in requirement_ids]
            need = PlannerNeed(
                id=need_id,
                objective=str(raw.get("objective") or "")[:600],
                capability=capability,
                depends_on=depends_on,
                input_requirements=[str(r) for r in raw.get("input_requirements", [])],
                desired_output=str(raw.get("desired_output") or ""),
                required_evidence=str(raw.get("required_evidence") or ""),
                constraints=constraints,
                criticality_hint=str(raw.get("criticality_hint") or "supporting"),
                requires=serves,
                origin="planner" if not existing else "repair",
            )
            accepted.append(need)
            seen_new.add(need_id)
            if len(accepted) >= max_needs:
                break
        return accepted, rejected

    def _fallback(self, goal: Goal, semantic) -> list[PlannerNeed]:
        """Deterministic minimal plan used only when no LLM is configured (offline tests)."""
        needs: list[PlannerNeed] = []
        if semantic.entities:
            needs.append(
                PlannerNeed(
                    id="n1",
                    objective="resolve mentioned entities to canonical ids",
                    capability="entity_resolution",
                    desired_output="PLAYER_ID_SET",
                    criticality_hint="core",
                    requires=[r.id for r in goal.requirements if r.kind.value == "ENTITY"],
                )
            )
        if semantic.analysis_requests:
            deps = ["n1"] if needs else []
            needs.append(
                PlannerNeed(
                    id="n2",
                    objective=semantic.analysis_requests[0].objective or semantic.analysis_requests[0].text,
                    capability="local_analytics",
                    depends_on=deps,
                    input_requirements=["PLAYER_ID_SET"] if deps else [],
                    desired_output="STATISTICAL_RESULT",
                    constraints=self._constraints_from_semantic(semantic),
                    criticality_hint="core",
                    requires=[r.id for r in goal.requirements if r.kind.value in {"ANALYSIS", "MEASURE", "SEASON", "GAME_POPULATION"}],
                )
            )
        if semantic.evidence_requests:
            needs.append(
                PlannerNeed(
                    id="n3",
                    objective=semantic.evidence_requests[0].objective or semantic.evidence_requests[0].text,
                    capability="web_research",
                    desired_output="WEB_EVIDENCE",
                    criticality_hint="core",
                    requires=[r.id for r in goal.requirements if r.kind.value == "EVIDENCE"],
                )
            )
        return needs

    def _constraints_from_semantic(self, semantic) -> dict[str, Any]:
        constraints: dict[str, Any] = {}
        for constraint in semantic.constraints:
            if constraint.kind == "season":
                constraints["season"] = constraint.value
            elif constraint.kind == "game_population":
                constraints["game_population"] = constraint.value
        return constraints


def derive_criticality(needs: list[PlannerNeed], goal: Goal) -> list[PlannerNeed]:
    """Deterministic criticality: CORE only if it serves a CORE requirement or is a
    transitive prerequisite of such a Need. Planner hints cannot raise criticality."""
    core_requirements = {r.id for r in goal.requirements if r.required}
    by_id = {n.id: n for n in needs}
    core_ids: set[str] = {n.id for n in needs if set(n.requires) & core_requirements}
    changed = True
    while changed:
        changed = False
        for need in needs:
            if need.id in core_ids:
                continue
            if any(dep in core_ids for dep in need.depends_on):
                core_ids.add(need.id)
                changed = True
    for need in needs:
        need.criticality = NeedCriticality.CORE if need.id in core_ids else NeedCriticality.SUPPORTING
    return needs
