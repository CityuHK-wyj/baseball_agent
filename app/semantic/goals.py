"""Goal and UserRequirement construction from a SemanticInterpretation.

UserRequirements own completion semantics. The Planner later creates PlannerNeeds; it may
never rewrite these requirements.

ADR 0016: a requirement's completion condition is semantic, not database-shaped. The
RequirementBuilder therefore records *what kind of answer completes the requirement*
(``RequirementCompletion``) instead of leaving the Judge to infer "database artifact" from
a field name. Open explanatory/historical questions are not forced into statistical
requirements; a measure mentioned as context becomes a supporting requirement.
"""

from __future__ import annotations

from typing import Any
import re

from app.capabilities.analytics.measures import MeasureResolution, resolve_texts
from app.contracts.models import (
    Constraint,
    EntityReference,
    Goal,
    RelationKind,
    RequirementCompletion,
    RequirementKind,
    SemanticInterpretation,
    UserRequirement,
)
from app.semantic.relations import build_relation_semantics, is_high_risk_text

# Finite set of native-record predicates the runtime knows how to inspect. An interpreter
# proposal outside this set is ignored, never executed or used for admission.
GOVERNED_PREDICATES = frozenset({"TEAM_MEMBERSHIP"})
from app.semantic.temporal import resolve_temporal_scope, scope_to_time_dict

NON_ENTITY_HINTS = {
    "concept", "metric", "stat", "statistic", "pitch_type", "dimension", "handedness",
    "term", "rule", "definition", "population", "event",
}

# A question that asks for the *meaning* of a term is knowledge work, not a statistic; a
# measure alias found there (e.g. "what is OPS?") must not become a data requirement.
_DEFINITION_MARKERS = (
    "什么是", "是什么", "怎么算", "如何计算", "怎么计算", "什么意思", "定义", "含义", "解释",
    "what is", "what are", "what does", "meaning", "definition", "how is", "how are",
)

# Explanatory / historical / contextual questions. A measure or a year appearing here is
# context for an explanation, not automatically a request for a statistical artifact.
# Deliberately excludes generic interrogatives ("如何", "怎么", "how did") because
# "打击表现如何" is a statistical evaluation request, not an explanation request.
_EXPLANATION_MARKERS = (
    "为什么", "为何", "原因", "解释", "介绍", "背景", "历史", "由来", "怎么回事",
    "分析一下", "讲一讲", "说说", "是什么原因", "怎么会这样",
    "why", "explain", "background", "history", "historical", "origin", "reason",
    "tell me about", "introduce", "what caused", "how come",
)

# An explicit request for a value. Only these (or an explicit measure constraint) make a
# measure mandatory on an otherwise explanatory question.
_VALUE_QUESTION_MARKERS = (
    "多少", "几次", "几个", "几支", "是多少", "how many", "how much",
    "what is his", "what are his", "what was his", "what were his",
)


def is_definition_question(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _DEFINITION_MARKERS)


def is_explanatory_question(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _EXPLANATION_MARKERS)


def wants_value(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _VALUE_QUESTION_MARKERS)


def is_high_risk(text: str) -> bool:
    lowered = (text or "").lower()
    return is_high_risk_text(lowered)


def _iso_anchor(anchor_date) -> str | None:
    if anchor_date is None:
        return None
    if hasattr(anchor_date, "isoformat"):
        return anchor_date.isoformat()[:10]
    return str(anchor_date)[:10]


class RequirementBuilder:
    def build(self, semantic: SemanticInterpretation, *, parent_goal: Goal | None = None, turn_type: str = "INDEPENDENT_GOAL", anchor_date=None) -> Goal:
        # Typed contextual proposals remain distinguishable from the current user's
        # interpreted requests. Assistant-only material cannot enter any request/measure scan.
        semantic = semantic.model_copy(update={
            "analysis_requests": [r for r in semantic.analysis_requests if r.meaning_origin != "ASSISTANT_CONTEXT"],
            "evidence_requests": [r for r in semantic.evidence_requests if r.meaning_origin != "ASSISTANT_CONTEXT"],
            "user_assertions": [r for r in semantic.user_assertions if r.meaning_origin == "CURRENT_USER_MESSAGE"],
        })
        goal = Goal(
            statement=semantic.understanding or semantic.raw_query,
            raw_query=semantic.raw_query,
            turn_type=turn_type,
            parent_goal_id=parent_goal.id if parent_goal else None,
        )
        # M2.5: the Goal anchor for relative temporal expressions is fixed once, at creation.
        goal.anchor_date = _iso_anchor(anchor_date)
        goal.original_time_scope = self._original_time_scope(semantic)
        goal.requirements = self._requirements(semantic)
        self._bind_analytical_source_clauses(goal)
        goal.resolved_time_scope = self._time_scope(semantic, goal.anchor_date)
        for requirement in goal.requirements:
            self._attach_temporal_scope(requirement, semantic, goal.anchor_date)
        self._bind_requirement_authority(goal)
        from app.semantic.provenance import bind_current_user_provenance
        bind_current_user_provenance(goal, parent_goal=parent_goal)
        from app.semantic.operations import ground_goal_operation_populations
        ground_goal_operation_populations(goal)
        from app.semantic.reporting import ground_goal_reporting_populations
        ground_goal_reporting_populations(goal)
        return goal

    @staticmethod
    def _bind_analytical_source_clauses(goal: Goal) -> None:
        from app.capabilities.analytics.conformance import _mandatory_clauses, role_from_text
        numeric = any(r.kind == RequirementKind.MEASURE and r.required and isinstance(r.value,dict) and r.value.get('derivable') for r in goal.requirements)
        # A semantic candidate must always be proven against the user's original wording,
        # never against the interpreter's paraphrase. Force clause binding when one exists.
        numeric = numeric or any(getattr(r, 'derivation_kind', '') == 'governed_semantic_candidate' for r in goal.requirements)
        numeric = numeric or bool(role_from_text(goal.raw_query) and re.search(r'performance|stats|表现|表現',goal.raw_query,re.I))
        numeric = numeric or bool(goal.parent_goal_id and any(r.kind == RequirementKind.ANALYSIS and r.completion == RequirementCompletion.GROUNDED for r in goal.requirements))
        if not numeric: return
        clauses=_mandatory_clauses(goal.raw_query)
        task_start=r'\s*(?:count|report|show|compute|calculate|explain|describe)\b|\s*(?:统计|統計|说明|說明)'
        independent=len(clauses)>1 and all(re.match(task_start,c,re.I) for c in clauses)
        for r in goal.requirements:
            if r.kind not in {RequirementKind.ANALYSIS,RequirementKind.CONSTRAINT} or r.completion == RequirementCompletion.EXPLANATION: continue
            # A governed semantic candidate carries structured obligations; its exact
            # source span is already recorded in the value. Binding the whole clause would
            # overwrite the structured value with raw text.
            if getattr(r, 'derivation_kind', '') == 'governed_semantic_candidate': continue
            source=goal.raw_query
            value = r.value if isinstance(r.value, dict) else {}
            source_text = str(value.get('source_text') or '').strip()
            if independent and r.kind == RequirementKind.ANALYSIS:
                keys={m.key for m in resolve_texts([str(r.value)]) if m.key}
                matching=[c for c in clauses if keys and keys <= {m.key for m in resolve_texts([c]) if m.key}]
                if len(matching)==1: source=matching[0]
                elif source_text: source=source_text
            elif r.kind == RequirementKind.ANALYSIS and source_text:
                # Prefer the exact user clause the interpreter bound to this task so a
                # local partition reviews its own wording, never an unrelated sibling.
                source=source_text
            r.bound_source_clause=source

    @staticmethod
    def _bind_requirement_authority(goal: Goal) -> None:
        """Bind interpreter-inferred Requirements to an authoritative parent.

        M2.12R3: no Requirement may define requested truth without explicit authority.
        The RequirementBuilder only derives from the user's own message, so an inferred
        constraint it produced is bound to the Goal's authoritative analysis/proposition
        Requirement. A Requirement that arrives later without such a parent remains
        unproven and cannot define analytical semantics.
        """
        from app.contracts.models import RequirementKind  # local import avoids cycle

        parents = [
            r.id
            for r in goal.requirements
            if r.required and r.kind in {RequirementKind.ANALYSIS, RequirementKind.EVIDENCE}
            and r.source == "user"
        ]
        for requirement in goal.requirements:
            if requirement.source != "inferred":
                continue
            if (requirement.kind == RequirementKind.GAME_POPULATION and requirement.value == 'REGULAR_SEASON'
                    and not re.search(r'postseason|playoffs|world series|spring training|季后赛|季後賽|世界大赛|世界大賽|春训|春訓',goal.raw_query,re.I)):
                requirement.derivation_kind='domain_default'
                requirement.derivation_proof='regular_season_competition_default'
                continue
            if requirement.derivation_kind == "governed_semantic_candidate":
                # An interpreter candidate is proven against every bound user
                # Requirement; only independently justified coverage, not its raw
                # source spans, can subtract user wording in the source review.
                requirement.parent_requirement_ids = [
                    r.id for r in goal.requirements if r.required and r.source == "user"
                ]
                requirement.derivation_proof = requirement.derivation_proof or (
                    "schema-grounded interpreter candidate with separately checked source coverage"
                )
                continue
            if not requirement.parent_requirement_ids:
                requirement.parent_requirement_ids = list(parents)
            if not requirement.derivation_kind:
                requirement.derivation_kind = "interpreter_requirement_binding"
            if not requirement.derivation_proof:
                requirement.derivation_proof = (
                    f"interpreter bound {requirement.kind.value} from the user message"
                )

    # ------------------------------------------------------------------ helpers
    def _requirements(self, semantic: SemanticInterpretation) -> list[UserRequirement]:
        raw = semantic.raw_query or semantic.understanding or ""
        explanatory = is_explanatory_question(raw)
        definitional = is_definition_question(raw)
        high_risk = is_high_risk(raw)
        value_request = wants_value(raw)
        # Background is allowed only for a non-high-risk explanation/definition, and never
        # as a substitute for a requested value.
        background_ok = (explanatory or definitional) and not high_risk and not value_request

        requirements: list[UserRequirement] = []
        for ref in self._entity_references(semantic):
            if ref.type_hint in NON_ENTITY_HINTS:
                # Statistics, dimensions and concepts are analysis inputs, not subjects.
                continue
            surface = ref.surface or ref.canonical_name or ""
            # Finite population-role vocabulary is not an individual identity. Do not
            # weaken Binder or attempt to resolve whole populations as named players.
            if not explanatory and re.fullmatch(
                r'(?:(?:top\s+\d+|all|MLB|left[ -]handed|right[ -]handed|L|R)\s+)?(?:batters?|hitters?|pitchers?|lefties|righties|southpaws)|所有(?:打者|投手)|[左右]投手?',
                surface.strip(), re.I):
                continue
            if not surface:
                continue
            if not explanatory:
                from app.capabilities.analytics.conformance import constraint_coverage
                dimension = constraint_coverage(surface)
                if dimension['state'] == 'COMPLETE' and dimension['obligations'] and set(dimension['obligations']) <= {'filters','group_by','qualification','analytical_role','limit','ordering'}:
                    continue
            value: dict[str, Any] = {
                "mention": surface,
                "type_hint": ref.type_hint,
                "canonical_name": ref.canonical_name,
                "canonical_id": ref.canonical_id,
                "namespace": ref.namespace,
                "status": ref.status.value,
                "carried": ref.is_contextual(),
                "identity_verified": ref.identity_verified,
            }
            value = {key: item for key, item in value.items() if item is not None}
            statement = f"Identify the entity mentioned as '{surface}'"
            if ref.canonical_name and ref.canonical_name != surface:
                statement += f" (carried identity: {ref.canonical_name})"
            requirements.append(
                UserRequirement(
                    kind=RequirementKind.ENTITY,
                    statement=statement,
                    required=True,
                    source="user",
                    references=[ref.reference] if ref.reference else [],
                    value=value,
                    completion=RequirementCompletion.IDENTITY,
                    entity_ref=ref,
                )
            )
        seen_season = False
        for constraint in semantic.constraints:
            if constraint.kind == 'season' and str(constraint.value).upper() in {'REGULAR_SEASON','POSTSEASON','WORLD_SERIES','SPRING_TRAINING'}:
                # "regular season" is competition, not a numeric year. Normalize
                # this finite interpreter representation without inventing a year.
                requirements.append(UserRequirement(kind=RequirementKind.GAME_POPULATION,
                    statement=f'Restrict the analysis to {constraint.value}',value=str(constraint.value).upper(),
                    source='user' if constraint.explicit else 'inferred',completion=RequirementCompletion.SCOPE))
            elif constraint.kind == "season":
                seen_season = True
                requirements.append(
                    UserRequirement(
                        kind=RequirementKind.SEASON,
                        statement=f"Restrict the analysis to the {constraint.value} season",
                        required=True,
                        source="user" if constraint.explicit else "inferred",
                        value=constraint.value,
                        completion=RequirementCompletion.SCOPE,
                    )
                )
            elif constraint.kind == "game_population":
                requirements.append(
                    UserRequirement(
                        kind=RequirementKind.GAME_POPULATION,
                        statement=f"Restrict the analysis to {constraint.value}",
                        required=True,
                        source="user" if constraint.explicit else "inferred",
                        value=str(constraint.value).upper(),
                        completion=RequirementCompletion.SCOPE,
                    )
                )
            elif constraint.kind in {"date_range", "time"}:
                requirements.append(
                    UserRequirement(
                        kind=RequirementKind.TIME,
                        statement=f"Restrict the analysis to {constraint.value!r}",
                        required=True,
                        value=constraint.value,
                        completion=RequirementCompletion.SCOPE,
                    )
                )
            elif constraint.kind == "qualification":
                requirements.append(
                    UserRequirement(
                        kind=RequirementKind.CONSTRAINT,
                        statement=f"Qualify results with {constraint.value!r}",
                        required=True,
                        value=constraint.value,
                        completion=RequirementCompletion.GROUNDED,
                    )
                )
            elif constraint.kind == "analytical":
                # An untrusted interpreter candidate (open-world wording -> semantic
                # dimensions). It is grounded against the governed catalog here, then
                # assessed for provenance and governed meaning in the authority resolver;
                # source coverage is separately checked by conformance. It never
                # becomes an authoritative obligation merely because the model said so.
                from app.semantic.analytical_candidate import ground_candidate

                candidate = constraint.value if isinstance(constraint.value, dict) else {}
                # A single exact user-cited analysis task bounds the candidate's
                # provenance. Keep each item's narrower attribution unchanged; this
                # conserves an extra modifier outside a narrowly cited restriction.
                if (len(semantic.analysis_requests) == 1 and candidate.get('source_text')
                        and semantic.analysis_requests[0].text in raw
                        and str(candidate['source_text']) in semantic.analysis_requests[0].text):
                    candidate = {**candidate, 'source_text': semantic.analysis_requests[0].text}
                role_only = bool(candidate.get("analytical_role")) and not any(
                    candidate.get(key)
                    for key in ("restrictions", "splits", "filters", "group_by", "ordering", "qualification", "limit")
                )
                # A role the current user message already states through an analysis
                # request adds no restriction. Dropping it prevents a redundant
                # candidate from raising a spurious clarification for a resolved role.
                established_roles = {str(a.analytical_role).upper() for a in semantic.analysis_requests
                                     if a.analytical_role}
                if role_only and str(candidate.get("analytical_role")).upper() in established_roles:
                    continue
                grounded, issues = ground_candidate(candidate, source_text="" if role_only else raw)
                if grounded:
                    # If the finite deterministic adapter already produces every obligation
                    # from the bound text, the candidate is redundant. Dropping it avoids a
                    # rejected duplicate poisoning an otherwise resolved clause.
                    from app.capabilities.analytics.conformance import constraint_coverage

                    base = constraint_coverage(raw)
                    base_obligations = base.get("obligations") or {}
                    redundant = True
                    for key, value in grounded.items():
                        if key in {"source_spans", "ignored_spans", "attributions"}:
                            continue
                        if key in {"filters", "group_by", "ordering", "ordering_tail", "tie_break_ordering"}:
                            if any(item not in base_obligations.get(key, []) for item in value):
                                redundant = False
                                break
                        elif base_obligations.get(key) != value:
                            redundant = False
                            break
                    if not redundant:
                        requirements.append(
                            UserRequirement(
                                kind=RequirementKind.CONSTRAINT,
                                statement=f"Interpreted analytical restriction: {grounded}",
                                required=True,
                                source="inferred",
                                value=grounded,
                                completion=RequirementCompletion.GROUNDED,
                                derivation_kind="governed_semantic_candidate",
                                derivation_proof=(
                                    "schema-grounded interpreter candidate with cited provenance; "
                                    "source residue requires separate conformance review"
                                ),
                            )
                        )
        if not seen_season:
            for expression in semantic.time_expressions:
                if expression.year:
                    requirements.append(
                        UserRequirement(
                            kind=RequirementKind.SEASON,
                            statement=f"Restrict the analysis to the {expression.year} season",
                            required=True,
                            value=expression.year,
                            completion=RequirementCompletion.SCOPE,
                        )
                    )
                    break
        for hint in semantic.population_hints:
            if not any(r.kind == RequirementKind.GAME_POPULATION and str(r.value).upper() == hint.upper() for r in requirements):
                requirements.append(
                    UserRequirement(
                        kind=RequirementKind.GAME_POPULATION,
                        statement=f"Restrict the analysis to {hint}",
                        required=True,
                        value=hint.upper(),
                        completion=RequirementCompletion.SCOPE,
                    )
                )
        for index, request in enumerate(semantic.analysis_requests):
            requirements.append(
                UserRequirement(
                    kind=RequirementKind.ANALYSIS,
                    statement=request.objective or request.text,
                    required=True,
                    source="user",
                    value={
                        "objective": request.objective or request.text,
                        "source_text": request.user_span or request.text,
                        "meaning_origin": request.meaning_origin,
                        "analytical_role": request.analytical_role,
                        "index": index,
                        "background_ok": background_ok,
                    },
                    completion=RequirementCompletion.EXPLANATION if (explanatory or definitional) else RequirementCompletion.GROUNDED,
                    background_ok=background_ok,
                    evidence_intent=request.evidence_intent,
                    relation_semantics=request.relation_semantics,
                    operation_contract=request.operation_contract,
                    reporting_contract=request.reporting_contract,
                )
            )
        # Requested measures must stay traceable to the final result. On an explanatory
        # question a measure found in the wording is context, so it is supporting unless the
        # user explicitly asked for a value.
        requirements.extend(
            self._measure_requirements(semantic, explanatory=explanatory or definitional, value_request=value_request)
        )
        for request in semantic.evidence_requests:
            requirements.append(
                UserRequirement(
                    kind=RequirementKind.EVIDENCE,
                    statement=request.objective or request.text,
                    required=True,
                    source="user",
                    value={"objective": request.objective or request.text, "source_text": request.user_span or request.text,
                           "meaning_origin": request.meaning_origin},
                    completion=RequirementCompletion.GROUNDED,
                    evidence_intent=request.evidence_intent,
                    relation_semantics=request.relation_semantics,
                )
            )
        # If the user asked something open but no analysis request was extracted, treat the
        # whole understanding as an analysis requirement so the Goal is not trivially met.
        if not any(r.kind in {RequirementKind.ANALYSIS, RequirementKind.EVIDENCE} for r in requirements):
            if semantic.analysis_requests or semantic.raw_query.strip():
                requirements.append(
                    UserRequirement(
                        kind=RequirementKind.ANALYSIS,
                        statement=semantic.raw_query,
                        required=True,
                        value={"objective": semantic.raw_query, "source_text": semantic.raw_query, "background_ok": background_ok},
                        completion=RequirementCompletion.EXPLANATION if (explanatory or definitional) else RequirementCompletion.GROUNDED,
                        background_ok=background_ok,
                    )
                )
        for assertion in semantic.user_assertions:
            requirements.append(
                UserRequirement(
                    kind=RequirementKind.CONSTRAINT,
                    statement=f"Verify the user's claim: {assertion.text}",
                    required=False,
                    source="user",
                    value={"assertion": assertion.text, "epistemic": "UNVERIFIED"},
                    completion=RequirementCompletion.GROUNDED,
                )
            )
        self._attach_relation_semantics(requirements, semantic)
        return requirements

    def _attach_relation_semantics(
        self, requirements: list[UserRequirement], semantic: SemanticInterpretation
    ) -> None:
        """Attach the immutable original intent and the canonical RelationSemantics.

        The normalized ``statement`` never overwrites the user's original request, and the
        machine interpretation lives in a separate ``relation_semantics`` field. This is the
        only place the interpretation is produced; downstream capabilities consume it.
        """
        raw = semantic.raw_query or semantic.understanding or ""
        scope = self._time_scope(semantic)
        entity_names: list[str] = []
        for requirement in requirements:
            if requirement.kind != RequirementKind.ENTITY:
                continue
            value = requirement.value if isinstance(requirement.value, dict) else {}
            name = value.get("canonical_name") or value.get("mention")
            if name:
                entity_names.append(str(name))
        population = scope.get("game_population")
        for requirement in requirements:
            if not requirement.original_intent:
                requirement.original_intent = self._original_intent_for(requirement, raw)
            proposed = requirement.relation_semantics
            value = requirement.value if isinstance(requirement.value, dict) else {}
            kind_hint = {
                RequirementKind.ENTITY: RelationKind.IDENTITY,
                RequirementKind.SEASON: RelationKind.SCOPE,
                RequirementKind.TIME: RelationKind.SCOPE,
                RequirementKind.GAME_POPULATION: RelationKind.SCOPE,
                RequirementKind.MEASURE: RelationKind.VALUE,
            }.get(requirement.kind)
            metric_refs: list[str] = []
            if requirement.kind == RequirementKind.MEASURE:
                metric_refs = [str(value.get("key") or value.get("label") or "")]
            meaning = build_relation_semantics(
                text=self._relation_text(requirement),
                kind=kind_hint,
                subjects=entity_names,
                metrics=metric_refs,
                time_scope=scope,
                population=population,
                original_intent=requirement.original_intent,
            )
            # The deterministic relation classifier is authoritative wherever it
            # recognizes the wording; an interpreter's proposal can only supplement an
            # otherwise-undecided relation, and can contribute a governed predicate. It
            # can never upgrade a rank/count question into a typed comparative claim.
            if proposed is not None:
                # Keep interpreted output units separate from population membership.
                # Neither this field nor subject references approve an aggregation.
                meaning.output_granularity = proposed.output_granularity
                if proposed.subject_refs:
                    # Reference IDs and names are exact bindings, not open-language
                    # matching. Unknown references cannot silently remove a subject.
                    refs = {e.ref: e.mention for e in semantic.entities if e.ref}
                    refs.update({e.reference.ref_id: e.surface for e in semantic.entity_references
                                 if e.reference and e.reference.ref_id})
                    allowed = {str((r.value or {}).get(k) or '').casefold()
                               for r in requirements if r.kind == RequirementKind.ENTITY
                               and isinstance(r.value, dict) for k in ('mention', 'canonical_name')}
                    bound_refs = [refs.get(ref, ref) for ref in proposed.subject_refs]
                    meaning.subject_refs = bound_refs
                    if any(ref.casefold() not in allowed for ref in bound_refs):
                        meaning.output_granularity = 'UNRESOLVED'
                if meaning.relation_kind == RelationKind.GENERAL and proposed.relation_kind != RelationKind.GENERAL:
                    proposed_kinds = list(proposed.relation_kinds) or [proposed.relation_kind]
                    meaning.relation_kind = proposed_kinds[0]
                    meaning.relation_kinds = proposed_kinds
                if proposed.predicate in GOVERNED_PREDICATES:
                    meaning.predicate = proposed.predicate
            requirement.relation_semantics = meaning

    @staticmethod
    def _original_intent_for(requirement: UserRequirement, raw: str) -> str:
        value = requirement.value if isinstance(requirement.value, dict) else {}
        if requirement.kind in {RequirementKind.ANALYSIS, RequirementKind.EVIDENCE}:
            return str(value.get("source_text") or raw)
        if requirement.kind == RequirementKind.ENTITY:
            return str(value.get("mention") or requirement.statement or raw)
        if requirement.kind == RequirementKind.MEASURE:
            return str(value.get("label") or requirement.statement or raw)
        return raw

    @staticmethod
    def _relation_text(requirement: UserRequirement) -> str:
        if requirement.kind in {RequirementKind.ANALYSIS, RequirementKind.EVIDENCE}:
            value = requirement.value if isinstance(requirement.value, dict) else {}
            return str(value.get("objective") or requirement.statement)
        return requirement.statement

    def _entity_references(self, semantic: SemanticInterpretation) -> list[EntityReference]:
        """Prefer the deterministic contextual references; fall back to raw mentions."""
        if semantic.entity_references:
            return list(semantic.entity_references)
        return [
            EntityReference(surface=entity.mention, type_hint=entity.type_hint)
            for entity in semantic.entities
        ]

    def _measure_requirements(
        self, semantic: SemanticInterpretation, *, explanatory: bool, value_request: bool
    ) -> list[UserRequirement]:
        raw = semantic.raw_query or ""
        if is_definition_question(raw):
            return []
        # Structured deliverables already own their VALUE slots. Operand measures are
        # execution dependencies, not extra requests to publish observations. A forecast
        # likewise does not request an independently observed historical statistic.
        local_requests = [a for a in semantic.analysis_requests
            if a.operation_contract is None and a.reporting_contract is None
            and not (a.evidence_intent and a.evidence_intent.mode == 'PREDICTION')]
        has_owned_deliverables = len(local_requests) != len(semantic.analysis_requests)
        if has_owned_deliverables and not local_requests:
            return []
        explicit_texts = [str(c.value) for c in semantic.constraints if c.kind == "measure" and c.value]
        if has_owned_deliverables:
            local_keys = {r.key for r in resolve_texts([a.objective or a.text for a in local_requests])}
            explicit_texts = [text for text in explicit_texts
                              if any(r.key in local_keys for r in resolve_texts([text]))]
        resolutions: list[MeasureResolution] = list(resolve_texts(explicit_texts))
        explicit_keys = {r.key for r in resolutions}
        seen = {r.key for r in resolutions}
        scan_texts: list[str] = [a.objective or a.text for a in local_requests]
        # The raw query is scanned too: the interpreter may paraphrase a measure out of the
        # objective. Definition questions are already excluded above.
        if not has_owned_deliverables:
            scan_texts.append(raw)
        # Inning restrictions describe row context, not the unsupported IP Measure.
        # Mask only successfully consumed finite inning spans; genuine innings-pitched
        # requests retain their existing unsupported Measure semantics.
        if not explanatory:
            from app.capabilities.analytics.conformance import _consume_inning_restriction
            cleaned=[]
            for text in scan_texts:
                spans=[]
                def consume(pattern, action, flags=re.I):
                    for m in re.finditer(pattern,text,flags):
                        part={}
                        action(m)
                        spans.append(m.span())
                _consume_inning_restriction(text, {}, consume)
                cleaned.append(''.join(' ' if any(a <= j < b for a,b in spans) else ch for j,ch in enumerate(text)))
            scan_texts=cleaned
        for resolution in resolve_texts(scan_texts):
            if resolution.key in seen:
                continue
            seen.add(resolution.key)
            resolutions.append(resolution)
        requirements: list[UserRequirement] = []
        for resolution in resolutions:
            # A measure is mandatory only when it was explicitly constrained or the user
            # asked for a value. Otherwise (e.g. "why did his home runs increase?") it is
            # supporting context for the explanation.
            required = (resolution.key in explicit_keys) or value_request or not explanatory
            requirements.append(
                UserRequirement(
                    kind=RequirementKind.MEASURE,
                    statement=f"Report the requested measure: {resolution.label}",
                    required=required,
                    source="user",
                    value=resolution.requirement_value(),
                    completion=RequirementCompletion.VALUE,
                )
            )
        return requirements

    @staticmethod
    def _original_time_scope(semantic: SemanticInterpretation) -> dict[str, Any]:
        return {
            "text": [
                {"text": e.text, "kind": e.kind, "year": e.year}
                for e in semantic.time_expressions
            ],
            "constraints": [c.model_dump() for c in semantic.constraints if c.kind in {"season", "date_range", "time"}],
        }

    def _attach_temporal_scope(
        self, requirement: UserRequirement, semantic: SemanticInterpretation, anchor_date: str | None
    ) -> None:
        """Resolve the Requirement's temporal scope once and preserve the original wording."""
        relation_kinds = []
        if requirement.relation_semantics is not None:
            relation_kinds = list(requirement.relation_semantics.relation_kinds) or [requirement.relation_semantics.relation_kind]
        text = self._relation_text(requirement)
        info = requirement.evidence_intent.information_class if requirement.evidence_intent else 'GENERIC'
        if (info == 'GENERIC' and requirement.relation_semantics is not None
                and requirement.relation_semantics.predicate == 'TEAM_MEMBERSHIP'):
            info = 'STATUS'
        resolved = resolve_temporal_scope(
            text,
            relation_kinds=relation_kinds,
            constraints=semantic.constraints,
            anchor_date=anchor_date,
            information_class_name=info if info != 'GENERIC' else None,
        )
        requirement.original_scope = {
            "text": resolved.original_text,
            "constraints": [c.model_dump() for c in semantic.constraints if c.kind in {"season", "date_range", "time"}],
        }
        requirement.resolved_scope = resolved

    def _time_scope(self, semantic: SemanticInterpretation, anchor_date: str | None = None) -> dict[str, Any]:
        scope: dict[str, Any] = {}
        for constraint in semantic.constraints:
            if constraint.kind == "season":
                if str(constraint.value).upper() in {'REGULAR_SEASON','POSTSEASON','WORLD_SERIES','SPRING_TRAINING'}:
                    scope['game_population']=str(constraint.value).upper()
                else:
                    scope["season"] = constraint.value
            elif constraint.kind == "date_range":
                value = constraint.value
                if isinstance(value, dict) and (value.get("start") or value.get("end")):
                    scope["date_range"] = value
                else:
                    # A relative expression (e.g. "recent") is resolved below, never left as a
                    # raw string for a downstream capability to reinterpret.
                    scope["original_date_range"] = value
            elif constraint.kind == "game_population":
                scope["game_population"] = str(constraint.value).upper()
        if semantic.population_hints:
            scope.setdefault("game_population", semantic.population_hints[0])
        # M2.5: when no explicit season/absolute range exists, attach the canonically resolved
        # relative window so every consumer sees the same machine-usable scope.
        if "season" not in scope and "date_range" not in scope:
            raw = semantic.raw_query or semantic.understanding or ""
            resolved = resolve_temporal_scope(
                raw, relation_kinds=(), constraints=semantic.constraints, anchor_date=anchor_date
            )
            resolved_dict = scope_to_time_dict(resolved)
            if "date_range" in resolved_dict:
                scope["date_range"] = resolved_dict["date_range"]
                scope["resolved_temporal"] = resolved.model_dump()
        return scope
