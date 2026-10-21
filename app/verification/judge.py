"""Independent Judge: does accepted evidence actually cover the user's requirements?

Tool success is not sufficiency. This module compares requested vs actual scope, checks
entity/time/population/measure/evidence channels, and produces RequirementAssessment plus a
GoalStatus and AnswerProjection.

ADR 0016/0017: completion is semantic. A scope channel can be pinned by any grounded
artifact, not only a database result. Claim admission is evaluated per artifact: one
unresolved requirement must not erase an independently supported claim.
"""

from __future__ import annotations

import re

from app.contracts.models import (
    AnswerProjection,
    AnswerProposition,
    Artifact,
    Claim,
    ComparisonCellSupport,
    ComparisonClaimSupport,
    CoverageState,
    EpistemicStatus,
    EvidenceFinding,
    Goal,
    GoalStatus,
    Hypothesis,
    PlannerNeed,
    PropositionKind,
    Reference,
    ReferenceType,
    RequirementCompletion,
    RequirementKind,
    QualifiedEvidence,
    RequirementAssessment,
    RequirementAuthority,
    RequirementStatus,
    Scope,
    UserRequirement,
    VerificationStatus,
)
from app.response.accepted_results import COMMUNICABLE_KINDS as _COMMUNICABLE_KINDS
from app.verification.propositions import content_tokens as _content_tokens_public
from app.verification.propositions import ensure_propositions
from app.verification.comparisons import prove_comparison
from app.verification.scope import compare_scope
from app.verification.coverage import requirement_coverage
from app.capabilities.analytics.conformance import receipt_conforms_goal, receipt_requirement_ids, receipt_usable, qualification, constraint_obligations, RequestedAnalyticalContract, ExecutionAttestation, window, contract_for_goal
from app.capabilities.analytics.predicates import canonical_population
from app.verification.lineage import span_resolves, text_hash, proposition_signature
from app.contracts.models import EvidenceSpan, RetrievedDocument
from app.knowledge.store import knowledge_class as _knowledge_class
from app.verification import source_inspection as _source_inspection
from app.verification.evidence_origin import index_origin_inputs, resolve_origins
from app.verification.evidence import finite_contradiction_hint as _finite_contradiction_hint
from app.verification.evidence_semantics import (
    apply_grounding_policy,
    assess_compatibility,
    deterministic_assessment,
    downgrade_funded_escalations,
    evidence_expectations,
    merge_assessments,
    request_assessment,
    request_semantic_conformance,
    validate_grounding_receipts,
)

_GROUNDED_KINDS = {"DATABASE_RESULT", "DERIVED_MEASURE", "WEB_EVIDENCE", "KNOWLEDGE_ITEM", "DYNAMIC_FACT"}
_STRUCTURED_KINDS = {"DATABASE_RESULT", "DERIVED_MEASURE", "KNOWLEDGE_ITEM", "DYNAMIC_FACT"}
# Obligations whose coverage comes from prose evidence verification rather than a
# structured measure/scope artifact.
_PROSE_KINDS = {
    PropositionKind.FACT,
    PropositionKind.EVENT,
    PropositionKind.COMPARISON,
    PropositionKind.EXPLANATION,
    PropositionKind.GROUNDED,
}


class Judge:
    def __init__(self, llm=None, max_inspections: int = 2):
        # Optional LLM for bounded source drill-down only. The Judge never uses it to bypass
        # a verifier hard gate or to create a Requirement.
        self.llm = llm
        self.max_inspections = max(0, int(max_inspections))
        self._inspections: list = []
        self._resolved_findings: dict[str, str] = {}

    def evaluate(
        self,
        goal: Goal,
        artifacts: list[Artifact],
        needs: list[PlannerNeed],
        *,
        clarification=None,
        blocked_needs: list[str] | None = None,
        hypotheses: list[Hypothesis] | None = None,
        continuation_exhausted: bool = False,
    ) -> AnswerProjection:
        # Database facts must carry an execution receipt conforming to current obligations.
        self._current_goal = goal
        self._current_artifacts = artifacts
        collected_artifacts = list(artifacts)
        self._collected_artifacts = collected_artifacts
        needs_by_id = {n.id: n for n in needs}
        from app.capabilities.analytics.authority import resolve_authority
        authoritative, _, _ = resolve_authority(
            [r.model_dump(mode="json") for r in goal.requirements],
            bound_user_message=goal.raw_query,
            clarification_deltas=[d.model_dump(mode="json") for d in goal.semantic_deltas],
            identity_evidence=[e.model_dump(mode="json") for a in artifacts for e in a.exports],
        )
        self._authoritative_ids = {r["id"] for r in authoritative}
        propositions = ensure_propositions(goal)
        from app.verification.governed_state import ExpectedObligations
        expected_obligations = ExpectedObligations.from_goal(goal)
        # A comparison may explicitly own two separately executed Requirement subsets.
        # Each side is re-bound against the current Goal's authoritative Requirements and
        # execution receipt; a Planner Need alone cannot grant comparison authority.
        self._comparison_proofs = {
            p.id: prove_comparison(goal, p, artifacts, needs_by_id)
            for p in propositions if p.kind == PropositionKind.COMPARISON
        }
        operation_owners = {r.id for r in goal.requirements if r.operation_contract is not None}
        reporting_owners = {r.id for r in goal.requirements if r.reporting_contract is not None}
        from app.verification.reporting import assess_reporting, reporting_scope_ids
        self._reporting_proofs = assess_reporting(goal, artifacts, propositions, self._authoritative_ids)
        local_support = {a.id:receipt_requirement_ids(a,goal,artifacts) - operation_owners - reporting_owners
                         for a in artifacts if a.kind == 'DATABASE_RESULT'}
        from app.verification.subjects import assess_subjects
        self._subject_proofs = assess_subjects(goal, artifacts, propositions, self._authoritative_ids)
        subject_owners = {p.requirement_id for p in propositions if p.subject_granularity is not None}
        for support in local_support.values():
            support.difference_update(subject_owners)
        for proof in self._subject_proofs.values():
            local_support.setdefault(proof['artifact_id'], set()).update({proof['requirement_id']} | {
                r.id for r in goal.requirements if r.id in proof['requested']['requirement_ids']
                and r.kind in {RequirementKind.SEASON, RequirementKind.TIME,
                               RequirementKind.GAME_POPULATION, RequirementKind.CONSTRAINT}})
        for proof in self._reporting_proofs.values():
            local_support.setdefault(proof['cell']['artifact_id'], set()).update(
                {proof['requirement_id']} | reporting_scope_ids(goal, proof))
        for proposition in propositions:
            proof = self._comparison_proofs.get(proposition.id)
            if not proof or proposition.comparison is None:
                continue
            for side, observation in ((proposition.comparison.lhs, proof.lhs),
                                      (proposition.comparison.rhs, proof.rhs)):
                if observation:
                    local_support.setdefault(observation.artifact_id, set()).update(side.requirement_ids)
        artifacts = [a for a in artifacts if a.kind != 'DATABASE_RESULT' or local_support.get(a.id)]
        self._hypotheses = list(hypotheses or [])
        self._subject_tokens = self._collect_subject_tokens(goal, artifacts)
        self._target_tokens = self._collect_target_tokens(goal)
        self._expected_propositions = {p.id: p for p in propositions
                                       if p.requirement_id in self._authoritative_ids}
        self._accepted_findings = {f.id: f for f in self._all_evidence_findings(artifacts, self._expected_propositions)}
        self._finding_owners = {
            f.id: artifact.id for artifact in artifacts
            for f in self._all_evidence_findings([artifact], self._expected_propositions)
        }
        # WSA Judge drill-down: bounded inspection of already-retrieved material can resolve
        # a soft semantic ambiguity before coverage is computed. Hard gates are never
        # re-opened and no new fetch is authorized.
        self._inspections = []
        self._resolved_findings = {}
        self._run_source_inspections(artifacts)
        # WSA-05: evidence governance only. This metadata never participates in coverage,
        # admission, or status computations below; no count can upgrade any hard gate.
        documents, spans = index_origin_inputs(artifacts)
        origin_assessments = []
        effective_findings = [self._effective_finding(f) for f in self._accepted_findings.values()]
        for proposition in propositions:
            origins = resolve_origins(proposition, effective_findings, documents, spans, llm=self.llm)
            if origins is not None:
                origin_assessments.append(origins)
        from app.verification.artifact_assessment import assess_artifacts
        self._artifact_assessments = assess_artifacts(
            collected_artifacts, goal, list(self._expected_propositions.values()),
            local_support, effective_findings, self._inspections)
        for proof in self._reporting_proofs.values():
            assessment = self._artifact_assessments[proof['cell']['artifact_id']]
            assessment.reporting_proofs.append(proof)
            assessment.eligible_proposition_ids = sorted(set(assessment.eligible_proposition_ids) | {proof['proposition_id']})
            assessment.status = 'ELIGIBLE'
        subject_ids = {p.id for p in propositions if p.subject_granularity is not None}
        for assessment in self._artifact_assessments.values():
            assessment.eligible_proposition_ids = sorted(set(assessment.eligible_proposition_ids) - subject_ids)
        for proof in self._subject_proofs.values():
            assessment = self._artifact_assessments[proof['artifact_id']]
            assessment.subject_proofs.append(proof)
            assessment.eligible_proposition_ids = sorted(set(assessment.eligible_proposition_ids) | {proof['proposition_id']})
            assessment.status = 'ELIGIBLE'
        states: list[RequirementAssessment] = []
        for requirement in goal.requirements:
            eligible = [a for a in artifacts if a.kind != 'DATABASE_RESULT'
                        or requirement.id in local_support.get(a.id, set())]
            state = (self._evaluate_requirement(requirement, eligible, needs_by_id)
                     if requirement.id in self._authoritative_ids else RequirementAssessment(
                         requirement_id=requirement.id, status=RequirementStatus.UNSATISFIED,
                         note="Requirement ancestry or governed derivation is unproved"))
            if requirement.reporting_contract is not None:
                proofs = [proof for proof in self._reporting_proofs.values()
                          if proof['requirement_id'] == requirement.id]
                state = RequirementAssessment(requirement_id=requirement.id,
                    status=(RequirementStatus.SATISFIED if len(proofs) == len(requirement.reporting_contract.cells)
                            else RequirementStatus.PARTIAL if proofs else RequirementStatus.UNSATISFIED),
                    support_artifact_ids=sorted({p['cell']['artifact_id'] for p in proofs}),
                    note='original reporting cells independently verified' if proofs else 'original reporting cells unproved')
            subject_children = [p for p in propositions if p.requirement_id == requirement.id
                                and p.subject_granularity is not None]
            if subject_children:
                proofs = [self._subject_proofs[p.id] for p in subject_children if p.id in self._subject_proofs]
                state = RequirementAssessment(requirement_id=requirement.id,
                    status=(RequirementStatus.SATISFIED if len(proofs) == len(subject_children)
                            else RequirementStatus.PARTIAL if proofs else RequirementStatus.UNSATISFIED),
                    support_artifact_ids=sorted({p['artifact_id'] for p in proofs}),
                    note='individual subject output units independently inspected' if proofs else
                         'requested subject/output granularity remains unproved')
            comparisons = [self._comparison_proofs[p.id] for p in propositions
                           if p.requirement_id == requirement.id and p.id in self._comparison_proofs
                           and p.comparison is not None]
            if len(comparisons) == 1:
                proof = comparisons[0]
                if proof.verified:
                    state = RequirementAssessment(requirement_id=requirement.id,
                        status=RequirementStatus.SATISFIED, support_artifact_ids=proof.artifact_ids,
                        note="typed comparison verified from two conformant scalar operands")
                elif proof.artifact_ids:
                    state = RequirementAssessment(requirement_id=requirement.id,
                        status=RequirementStatus.PARTIAL, support_artifact_ids=proof.artifact_ids,
                        note="comparison operand or relation remains unproved")
            support = [a for a in artifacts if a.id in state.support_artifact_ids]
            if state.status == RequirementStatus.SATISFIED and support and all(
                (a.kind == "DATABASE_RESULT" and a.structured_data["execution_receipt"]["conformance"]["status"] != "MATCH") or (a.kind == "DERIVED_MEASURE" and a.structured_data.get("source_coverage_complete") is False) for a in support
            ):
                state.status = RequirementStatus.PARTIAL
                state.note += "; executed source coverage is partial"
            states.append(state)

        core_states = [s for s, r in zip(states, goal.requirements) if r.required]
        background_ok = self._background_allowed(goal)
        claims = self._claims(goal, artifacts, needs_by_id, states, local_support)
        for proposition in propositions:
            proof = self._comparison_proofs.get(proposition.id)
            if not proof or not proof.verified or not proof.lhs or not proof.rhs:
                continue
            def describe(observation):
                contract = observation.requested
                subjects = ','.join(f"{entity.role}:{','.join(map(str,entity.ids))}"
                                    for entity in contract.entities)
                return (f"{observation.measure_key}[MLBAM {subjects}; season={contract.season}; "
                        f"window={contract.date_range}; population={contract.game_types}; "
                        f"filters={contract.filters}; qualification={contract.qualification}]")
            claims.append(Claim(
                text=(f"Verified comparison: lhs {describe(proof.lhs)} = {proof.lhs.value}; "
                      f"rhs {describe(proof.rhs)} = {proof.rhs.value}; "
                      f"lhs {proposition.comparison.relation.value} rhs"),
                support_refs=[Reference(ref_type=ReferenceType.ARTIFACT, ref_id=aid, source="comparison_operand")
                              for aid in proof.artifact_ids],
                requirement_ids=[proposition.requirement_id], proposition_ids=[proposition.id],
                epistemic=EpistemicStatus.SUPPORTED,
                comparison_support=ComparisonClaimSupport(
                    relation=proposition.comparison.relation,
                    lhs=ComparisonCellSupport(artifact_id=proof.lhs.artifact_id,
                        requirement_ids=proof.lhs.requirement_ids, row_index=0,
                        column=proof.lhs.column, measure_key=proof.lhs.measure_key,
                        value=str(proof.lhs.value)),
                    rhs=ComparisonCellSupport(artifact_id=proof.rhs.artifact_id,
                        requirement_ids=proof.rhs.requirement_ids, row_index=0,
                        column=proof.rhs.column, measure_key=proof.rhs.measure_key,
                        value=str(proof.rhs.value))),
            ))
        # ADR 0021: propositions refine the Requirement graph; their coverage (derived
        # from RequirementAssessment plus proposition-specific EvidenceFindings) is the sole
        # authority behind GoalStatus. No parallel completion structure is introduced.
        self._apply_proposition_coverage(propositions, states, artifacts, claims)
        status = self._status_from_coverage(propositions, clarification, background_ok)
        assessments = self._requirement_assessments(goal, states, propositions, claims)
        if continuation_exhausted and status == GoalStatus.FAILED:
            # A bounded evidence continuation was actually attempted; incompleteness is
            # governed here, not by rewriting a previously published projection.
            status = GoalStatus.LIMITED

        # WSA-03/presentation: relevant but non-authoritative or otherwise insufficient evidence
        # may be shown with a structural limitation. This never changes coverage, Claims or
        # GoalStatus - presentation cannot launder authority.
        qualified = self._collect_qualified_evidence(propositions)
        limitations = self._limitations(goal, states)
        limitations.extend(self._proposition_limitations(propositions))
        if continuation_exhausted and status != GoalStatus.COMPLETE:
            limitations.append("bounded post-Judge acquisition exhausted; mandatory meaning remains unresolved")
        unresolved = [need.id for need in needs if need.criticality.value == "CORE" and need.status.value in {"UNSATISFIED", "BLOCKED"}]
        unresolved.extend(blocked_needs or [])
        support_ids = sorted({aid for state in states for aid in state.support_artifact_ids})
        projection = AnswerProjection(
            goal_id=goal.id,
            goal_status=status,
            claims=claims,
            limitations=limitations,
            unresolved_need_ids=sorted(set(unresolved)),
            clarification=clarification,
            supporting_artifact_ids=support_ids,
            background_ok=background_ok,
            background_topic=goal.statement or goal.raw_query,
            hypotheses=list(getattr(self, "_hypotheses", [])),
            propositions=propositions,
            source_inspections=list(self._inspections),
            qualified_evidence=qualified,
            evidence_origins=origin_assessments,
            requirement_assessments=assessments,
            artifact_assessments=list(self._artifact_assessments.values()),
        )
        from app.verification.governed_state import (
            reaggregate_assessments, validate_governed_projection, GovernedStateError)
        projection.requirement_assessments = reaggregate_assessments(projection)
        try:
            validate_governed_projection(projection,expected=expected_obligations)
        except GovernedStateError as exc:
            # Preserve lifecycle's one fresh Judge pass and detached LIMITED recovery,
            # even when finalization itself detects the inconsistency.
            exc.projection = projection.frozen_snapshot()
            raise
        return projection.frozen_snapshot()

    def _requirement_assessments(self, goal, states, propositions, claims):
        assessments = []
        for requirement, state in zip(goal.requirements, states):
            children = [p for p in propositions if requirement.id in p.owner_requirement_ids]
            coverage = requirement_coverage(children)
            from app.verification.coverage import status_for_coverage
            status = status_for_coverage(coverage)
            assessments.append(RequirementAssessment(
                requirement_id=requirement.id, status=status, coverage=coverage,
                authority_status=(RequirementAuthority.AUTHORITATIVE
                                  if requirement.id in self._authoritative_ids else RequirementAuthority.UNPROVEN),
                support_artifact_ids=sorted({aid for p in children for aid in p.supporting_artifact_ids}),
                supporting_exports=list(state.supporting_exports),
                support_refs=[ref for claim in claims
                              if any(p.id in claim.proposition_ids for p in children)
                              for ref in claim.support_refs],
                gaps=list(state.gaps),
                coverage_gaps=list(dict.fromkeys(gap for p in children for gap in p.gaps)),
                conflicts=[ref for p in children for ref in p.contradicting_claim_ids],
                inspection_ids=[inspection.id for inspection in self._inspections
                                if inspection.proposition_id in {p.id for p in children}],
                note=state.note,
            ))
        return assessments

    # -------------------------------------------------------- proposition coverage
    def _collect_qualified_evidence(self, propositions) -> list[QualifiedEvidence]:
        """Relevant, provenance-intact evidence the Judge may show but not assert (WSA-03)."""
        prop_by_id = {p.id: p for p in propositions}
        span_by_id: dict[str, dict] = {}
        documents: dict[str, dict] = {}
        for artifact in getattr(self, "_current_artifacts", []) or []:
            data = artifact.structured_data or {}
            for span in data.get("evidence_spans") or []:
                if isinstance(span, dict) and span.get("id"):
                    span_by_id[str(span["id"])] = span
            for document in data.get("documents") or []:
                if isinstance(document, dict) and document.get("id"):
                    documents[str(document["id"])] = document
        qualified: list[QualifiedEvidence] = []
        seen: set[tuple[str, str, str]] = set()
        for finding in list(self._accepted_findings.values()):
            finding = self._effective_finding(finding)
            proposition = prop_by_id.get(finding.proposition_id)
            if proposition is None or finding.status == VerificationStatus.SUPPORTED:
                continue
            # Hard conflicts, wrong entity/population, irrelevant spans and structurally
            # invalid provenance stay internal. A soft scope/time-unknown is allowed: stale
            # or undated material may be shown as explicitly limited historical context.
            if not finding.checks.get("target_ok", False):
                continue
            if finding.checks.get("subject") is False or finding.hard_conflict or not finding.admissible:
                continue
            span = span_by_id.get(finding.span_id)
            document = documents.get(finding.document_id)
            if not span or not document or not _qualified_integrity(finding, span, document):
                continue
            key = (finding.proposition_id, finding.document_id, finding.span_id)
            if key in seen:
                continue
            seen.add(key)
            kind, limitation = _qualification(finding)
            if limitation is None:
                continue
            qualified.append(
                QualifiedEvidence(
                    proposition_id=finding.proposition_id,
                    requirement_id=proposition.requirement_id or "",
                    finding_id=finding.id,
                    span_id=finding.span_id,
                    document_id=finding.document_id,
                    source_url=finding.source_url,
                    source_title=str(document.get("title") or ""),
                    retrieved_at=finding.retrieved_at,
                    publication_date=document.get("publication_date"),
                    span_text=str(span.get("text") or ""),
                    relation=finding.relation_state,
                    assertion_strength=finding.assertion_state,
                    temporal_state=finding.temporal_state,
                    authority_need=(proposition.authority_expectation.need.value
                                    if proposition.authority_expectation else ""),
                    source_role=(finding.semantic_assessment.get("source_role") or {}
                                 if isinstance(finding.semantic_assessment, dict) else {}),
                    kind=kind,
                    limitation=limitation,
                )
            )
        # WSA-07: cached mutable knowledge is evidence, not stable truth. It may be shown with
        # its provenance and a limitation, but it never satisfies a Requirement on its own.
        for artifact in getattr(self, "_current_artifacts", []) or []:
            if artifact.kind != "KNOWLEDGE_ITEM":
                continue
            for item in artifact.structured_data.get("items", []) or []:
                if _knowledge_class(str(item.get("type", ""))) != "MUTABLE_FACT":
                    continue
                refs = [str(ref) for ref in (item.get("source_refs") or []) if ref]
                qualified.append(
                    QualifiedEvidence(
                        kind="MUTABLE_KNOWLEDGE",
                        span_text=str(item.get("summary") or item.get("title") or ""),
                        source_url=refs[0] if refs else "",
                        source_title=str(item.get("title") or ""),
                        source_role={"source_class": "STRUCTURED", "content_role": "PRIMARY_RECORD",
                                     "evidential_basis": "UNKNOWN"},
                        limitation=(
                            "cached mutable knowledge with no independently verified evidence in "
                            "this run; it does not establish the current fact"
                        ),
                    )
                )
        # A typed comparison that is not yet proved may still expose whichever operand was
        # verified. It is useful context, never a comparative conclusion (GA-07 intact).
        for proposition in propositions:
            if proposition.kind != PropositionKind.COMPARISON:
                continue
            proof = self._comparison_proofs.get(proposition.id)
            if proof is None or proof.verified or proof.contradicted:
                continue
            for observed in (proof.lhs, proof.rhs):
                if observed is None:
                    continue
                qualified.append(
                    QualifiedEvidence(
                        proposition_id=proposition.id,
                        requirement_id=proposition.requirement_id or "",
                        kind="PARTIAL_OPERAND",
                        span_text=f"{observed.measure_key} = {observed.value}",
                        limitation=(
                            "the comparison requires two independently verified operands; "
                            "only one is currently verified, so no comparison can be established"
                        ),
                    )
                )
        return qualified

    def _status_from_coverage(self, propositions, clarification, background_ok) -> GoalStatus:
        from app.verification.coverage import goal_status
        resolution = getattr(getattr(self,'_current_goal',None),'analytical_resolution_state',{})
        investigated = any(c.get('state') in {'PARTIAL','UNRESOLVED'} for c in resolution.get('current',{}).values())
        return goal_status(propositions,clarification=clarification,
                           background_ok=background_ok,investigated=investigated)

    def _apply_proposition_coverage(self, propositions, states, artifacts, claims) -> None:
        states_by_req = {state.requirement_id: state for state in states}
        findings = [self._effective_finding(finding) for finding in self._accepted_findings.values()]
        findings_by_prop: dict[str, list[EvidenceFinding]] = {}
        for finding in findings:
            findings_by_prop.setdefault(finding.proposition_id, []).append(finding)
        verification_ran = any(
            artifact.kind == "WEB_EVIDENCE"
            and (artifact.structured_data or {}).get("proposition_verification") is True
            for artifact in artifacts
        )
        artifacts_by_id = {artifact.id: artifact for artifact in artifacts}
        self._link_claims_to_propositions(claims, states, propositions)
        claims_by_prop: dict[str, list[Claim]] = {}
        for claim in claims:
            for proposition_id in claim.proposition_ids:
                claims_by_prop.setdefault(proposition_id, []).append(claim)
        for proposition in propositions:
            state = states_by_req.get(proposition.requirement_id)
            coverage = _state_to_coverage(state)
            support_artifacts = list(state.support_artifact_ids) if state else []
            proposition.supporting_artifact_ids = support_artifacts
            proposition.supporting_claim_ids = [claim.id for claim in claims_by_prop.get(proposition.id, [])]
            proposition.evidence_finding_ids = [f.id for f in findings_by_prop.get(proposition.id, [])]
            proposition.contradicting_claim_ids = []
            proposition.gaps = []
            if proposition.kind in _PROSE_KINDS:
                prop_findings = findings_by_prop.get(proposition.id, [])
                if prop_findings:
                    coverage = _coverage_from_findings(prop_findings)
                    proposition.contradicting_claim_ids = [
                        f.id for f in prop_findings if f.status == VerificationStatus.CONTRADICTED
                    ]
                elif verification_ran:
                    # Prose verification ran for this Goal. Only a requirement state the
                    # Judge already found SATISFIED by a structured artifact may stay
                    # SATISFIED; a merely PARTIAL candidate (e.g. an on-topic but generic
                    # curated definition) must never be upgraded by artifact kind alone.
                    if state is not None and state.status == RequirementStatus.SATISFIED:
                        structured = [
                            aid for aid in support_artifacts
                            if artifacts_by_id.get(aid) is not None
                            and artifacts_by_id[aid].kind in _STRUCTURED_KINDS
                        ]
                        if structured:
                            coverage = CoverageState.SATISFIED
                            proposition.supporting_artifact_ids = structured
                        else:
                            coverage = CoverageState.PARTIAL
                            proposition.gaps.append("grounded prose was not specific to this obligation")
                    elif state is not None and state.status == RequirementStatus.PARTIAL:
                        coverage = CoverageState.PARTIAL
                    else:
                        coverage = CoverageState.UNSUPPORTED
                        proposition.gaps.append("retrieved sources did not establish this obligation")
            # Native-record admission is proposition-local even when one Requirement
            # decomposes into multiple answer slots. A matching sibling cannot certify it.
            native_ids = [aid for aid in support_artifacts if aid in self._artifact_assessments
                          and self._artifact_assessments[aid].source_kind == 'STRUCTURED_PROVIDER']
            if native_ids:
                admissible = [aid for aid in support_artifacts if aid not in native_ids
                              or proposition.id in self._artifact_assessments[aid].eligible_proposition_ids]
                proposition.supporting_artifact_ids = admissible
                if not admissible and not proposition.supporting_claim_ids:
                    coverage = CoverageState.UNSUPPORTED
                    proposition.gaps.append('native record does not establish this predicate/subject/time slot')
            if proposition.kind == PropositionKind.COMPARISON:
                proof = self._comparison_proofs.get(proposition.id)
                if proof and proof.verified:
                    coverage = CoverageState.SATISFIED
                    proposition.supporting_artifact_ids = proof.artifact_ids
                    proposition.evidence_finding_ids = []
                    proposition.contradicting_claim_ids = []
                else:
                    # Neither a conformant scalar receipt nor a prose finding proves both
                    # operands and their requested relation. The web verifier's SUPPORT
                    # is scope/token relevance, not typed comparative entailment.
                    if coverage == CoverageState.SATISFIED:
                        coverage = CoverageState.PARTIAL
                    proposition.gaps.append("comparison operands and relation are not verified")
                    proposition.supporting_claim_ids = []
            if proposition.reporting_index is not None:
                proof = self._reporting_proofs.get(proposition.id)
                coverage = CoverageState.SATISFIED if proof else CoverageState.UNSUPPORTED
                proposition.supporting_artifact_ids = [proof['cell']['artifact_id']] if proof else []
                proposition.evidence_finding_ids = []
                proposition.supporting_claim_ids = [c.id for c in claims_by_prop.get(proposition.id, [])] if proof else []
                if not proof:
                    proposition.gaps.append('exact requested original observation lacks approved scalar receipt')
            if proposition.subject_granularity is not None:
                proof = self._subject_proofs.get(proposition.id)
                coverage = (CoverageState.SATISFIED if proof and proof['complete'] else
                            CoverageState.PARTIAL if proof else CoverageState.UNSUPPORTED)
                proposition.supporting_artifact_ids = [proof['artifact_id']] if proof else []
                proposition.supporting_claim_ids = [c.id for c in claims_by_prop.get(proposition.id, [])] if proof else []
                proposition.evidence_finding_ids = []
                if not proof:
                    proposition.gaps.append('requested subject/output granularity lacks an approved individual evidence unit')
            if coverage == CoverageState.SATISFIED and not (
                proposition.supporting_claim_ids
                or proposition.supporting_artifact_ids
                or proposition.evidence_finding_ids
            ):
                coverage = CoverageState.PARTIAL
                proposition.gaps.append("support recorded at requirement level but not tied to a claim or span")
            # Coverage and Claim admission must not use divergent semantics: a satisfied
            # communicable obligation that no approved Claim states is not presentable.
            # Downgrade it here, at the semantic owner, instead of relying on the
            # last-resort integrity guard to reject an otherwise-complete projection.
            if (coverage == CoverageState.SATISFIED and proposition.kind in _COMMUNICABLE_KINDS
                    and not proposition.supporting_claim_ids):
                coverage = CoverageState.PARTIAL
                proposition.gaps.append("satisfied communicable obligation has no approved Claim")
            if (not proposition.owner_requirement_ids
                    or not set(proposition.owner_requirement_ids) <= self._authoritative_ids):
                coverage = CoverageState.UNSUPPORTED
                proposition.supporting_claim_ids = []
                proposition.supporting_artifact_ids = []
                proposition.gaps.append("Requirement ancestry or governed derivation is unproved")
            proposition.coverage = coverage
            proposition.note = _coverage_note(proposition, state)

    def _link_claims_to_propositions(self, claims, states, propositions) -> None:
        artifact_to_reqs: dict[str, set[str]] = {}
        for state in states:
            for artifact_id in state.support_artifact_ids:
                artifact_to_reqs.setdefault(artifact_id, set()).add(state.requirement_id)
        req_to_props: dict[str, list[str]] = {}
        for proposition in propositions:
            # A claim from a value-bearing Artifact is not automatically a comparative
            # claim merely because it serves the same Requirement.
            if (proposition.requirement_id and proposition.kind != PropositionKind.COMPARISON
                    and proposition.reporting_index is None and proposition.subject_granularity is None):
                req_to_props.setdefault(proposition.requirement_id, []).append(proposition.id)
        for claim in claims:
            proposition_ids = set(claim.proposition_ids)
            # The approved typed comparison Claim has its own explicit proposition and
            # two operand refs. It must not be reused as proof of sibling Requirements.
            if any(p.id in proposition_ids and p.kind == PropositionKind.COMPARISON
                   for p in propositions):
                continue
            if any(p.id in proposition_ids and p.subject_granularity is not None for p in propositions):
                allowed = {proof['proposition_id'] for ref in claim.support_refs
                           if ref.ref_type == ReferenceType.ARTIFACT and ref.ref_id in self._artifact_assessments
                           for proof in self._artifact_assessments[ref.ref_id].subject_proofs}
                claim.proposition_ids = sorted(proposition_ids & allowed)
                continue
            if any(p.id in proposition_ids and p.reporting_index is not None for p in propositions):
                allowed = {proof['proposition_id'] for ref in claim.support_refs
                           if ref.ref_type == ReferenceType.ARTIFACT and ref.ref_id in self._artifact_assessments
                           for proof in self._artifact_assessments[ref.ref_id].reporting_proofs}
                claim.proposition_ids = sorted(proposition_ids & allowed)
                continue
            derived = [self._artifact_assessments[ref.ref_id] for ref in claim.support_refs
                       if ref.ref_type == ReferenceType.ARTIFACT and ref.ref_id in self._artifact_assessments
                       and self._artifact_assessments[ref.ref_id].source_kind == 'DERIVED']
            if derived:
                allowed = {pid for assessment in derived for pid in assessment.eligible_proposition_ids}
                claim.proposition_ids = sorted(proposition_ids & allowed)
                continue
            native = [self._artifact_assessments[ref.ref_id] for ref in claim.support_refs
                      if ref.ref_type == ReferenceType.ARTIFACT and ref.ref_id in self._artifact_assessments
                      and self._artifact_assessments[ref.ref_id].source_kind == 'STRUCTURED_PROVIDER']
            if native:
                allowed = {pid for assessment in native for pid in assessment.eligible_proposition_ids}
                claim.proposition_ids = sorted(proposition_ids & allowed)
                continue
            for ref in claim.support_refs:
                if ref.ref_type != ReferenceType.ARTIFACT:
                    continue
                for req_id in artifact_to_reqs.get(ref.ref_id, set()):
                    proposition_ids.update(req_to_props.get(req_id, []))
            claim.proposition_ids = sorted(proposition_ids)

    @staticmethod
    def _all_evidence_findings(artifacts, expected=None) -> list[EvidenceFinding]:
        findings: list[EvidenceFinding] = []
        for artifact in artifacts:
            if artifact.kind != "WEB_EVIDENCE":
                continue
            data = artifact.structured_data or {}
            span_by_id = {
                str(span.get("id")): span
                for span in (data.get("evidence_spans") or [])
                if isinstance(span, dict)
            }
            documents = {str(d.get("id")): d for d in data.get("documents", []) if isinstance(d, dict)}
            for raw in data.get("evidence_findings", []) or []:
                try:
                    finding = EvidenceFinding.model_validate(raw)
                except Exception:  # noqa: BLE001 - a malformed record must not break the Judge
                    continue
                # When the artifact declares its span collection (even empty), the finding
                # must resolve to a persisted span.
                if not _finding_resolves(finding, span_by_id, documents):
                    continue
                if expected is not None:
                    proposition = expected.get(finding.proposition_id)
                    if proposition is None or finding.proposition_signature != proposition_signature(proposition):
                        continue
                    if artifact.actual_scope and proposition.time_scope.get("season") and artifact.actual_scope.season not in {None, proposition.time_scope["season"]}:
                        continue
                    if artifact.actual_scope and proposition.population and artifact.actual_scope.game_population not in {None, proposition.population}:
                        continue
                findings.append(finding)
        return findings

    def _proposition_limitations(self, propositions) -> list[str]:
        notes: list[str] = []
        for proposition in propositions:
            if not proposition.mandatory:
                continue
            if proposition.coverage == CoverageState.CONTRADICTED:
                notes.append(f"contradicted — {proposition.statement} (retrieved evidence conflicts)")
            elif proposition.coverage == CoverageState.UNSUPPORTED and proposition.gaps:
                notes.append(f"not covered — {proposition.statement} ({'; '.join(proposition.gaps)})")
            elif proposition.coverage == CoverageState.PARTIAL and proposition.gaps:
                notes.append(f"partial — {proposition.statement} ({'; '.join(proposition.gaps)})")
        return notes

    def _completion(self, requirement: UserRequirement) -> RequirementCompletion:
        if requirement.completion is not None:
            return requirement.completion
        return {
            RequirementKind.ENTITY: RequirementCompletion.IDENTITY,
            RequirementKind.SEASON: RequirementCompletion.SCOPE,
            RequirementKind.TIME: RequirementCompletion.SCOPE,
            RequirementKind.GAME_POPULATION: RequirementCompletion.SCOPE,
            RequirementKind.MEASURE: RequirementCompletion.VALUE,
            RequirementKind.ANALYSIS: RequirementCompletion.GROUNDED,
            RequirementKind.EVIDENCE: RequirementCompletion.GROUNDED,
        }.get(requirement.kind, RequirementCompletion.GROUNDED)

    def _evaluate_requirement(
        self, requirement: UserRequirement, artifacts: list[Artifact], needs_by_id
    ) -> RequirementAssessment:
        if requirement.kind == RequirementKind.ENTITY:
            return self._entity_state(requirement, artifacts)
        if requirement.kind == RequirementKind.SEASON:
            return self._scope_state(requirement, artifacts, "season")
        if requirement.kind == RequirementKind.GAME_POPULATION:
            return self._scope_state(requirement, artifacts, "population")
        if requirement.kind == RequirementKind.TIME:
            return self._scope_state(requirement, artifacts, "time")
        if requirement.kind == RequirementKind.ANALYSIS:
            return self._analysis_state(requirement, artifacts, needs_by_id)
        if requirement.kind == RequirementKind.MEASURE:
            return self._measure_state(requirement, artifacts, needs_by_id)
        if requirement.kind == RequirementKind.EVIDENCE:
            return self._evidence_state(requirement, artifacts, needs_by_id)
        return self._constraint_state(requirement, artifacts, needs_by_id)

    def _collect_subject_tokens(self, goal: Goal, artifacts: list[Artifact]) -> set[str]:
        tokens: set[str] = set()
        for requirement in goal.requirements:
            if requirement.kind == RequirementKind.ENTITY and isinstance(requirement.value, dict):
                tokens.update(_tokens(requirement.value.get("mention", "")))
        for artifact in artifacts:
            if artifact.kind != "ENTITY_RESOLUTION":
                continue
            for entry in artifact.structured_data.get("resolved", []) + artifact.structured_data.get("candidates", []):
                tokens.update(_tokens(entry.get("name", "")))
                tokens.update(_tokens(entry.get("mention", "")))
        # Concept questions ("what is OPS?") have no entity subject. Fall back to the
        # analysis objective so web findings are still checked against the topic.
        if not tokens:
            for requirement in goal.requirements:
                if requirement.kind in {RequirementKind.ANALYSIS, RequirementKind.EVIDENCE} and requirement.required:
                    tokens.update(_tokens(requirement.statement))
        return tokens

    def _collect_target_tokens(self, goal: Goal) -> set[str]:
        """Machine-checkable information needs (measure keys/labels).

        A page that mentions the subject but not the requested measure is not evidence for
        the question. We only apply this additional gate for measure requests, where the
        required information is unambiguous; for open questions subject relevance is the
        appropriate bar.
        """
        tokens: set[str] = set()
        for requirement in goal.requirements:
            if requirement.kind == RequirementKind.MEASURE and requirement.required and isinstance(requirement.value, dict):
                tokens.update(_tokens(requirement.value.get("label", "")))
                key = requirement.value.get("key")
                if key:
                    tokens.update(_tokens(str(key).replace("_", " ")))
        tokens.discard("")
        return tokens

    def _candidate_artifacts(self, requirement, artifacts, needs_by_id, kinds=None) -> list[Artifact]:
        serving = {
            need_id
            for need_id, need in needs_by_id.items()
            if requirement.id in need.requires
        }
        candidates = [a for a in artifacts if a.need_id in serving and a.status in {"ACCEPTED", "PARTIAL"}]
        if not candidates:
            candidates = [a for a in artifacts if a.status in {"ACCEPTED", "PARTIAL"}]
        if kinds:
            candidates = [a for a in candidates if a.kind in kinds]
        # Native records must have Judge-inspected proposition-local bindings.
        # Shared subject tokens or a Planner Need never establish the requested relation.
        def independently_admissible(artifact):
            if requirement.operation_contract is not None and artifact.kind != 'DERIVED_MEASURE':
                return False  # an operand observation is not its requested operation
            if artifact.kind not in {'DYNAMIC_FACT', 'DERIVED_MEASURE'}:
                return True
            assessment = self._artifact_assessments.get(artifact.id)
            if assessment is None or assessment.status not in {'ELIGIBLE', 'QUALIFIED'}:
                return False
            return any(p.requirement_id == requirement.id and p.id in assessment.eligible_proposition_ids
                       for p in self._expected_propositions.values())

        return [a for a in candidates if independently_admissible(a)]

    def _grounded_artifacts(self, artifacts: list[Artifact]) -> list[Artifact]:
        return [a for a in artifacts if a.status in {"ACCEPTED", "PARTIAL"} and a.kind in _GROUNDED_KINDS
                and (a.kind not in {'DYNAMIC_FACT','DERIVED_MEASURE'}
                     or self._artifact_assessments[a.id].eligible_proposition_ids)]

    def _entity_state(self, requirement, artifacts) -> RequirementAssessment:
        mention = (requirement.value or {}).get("mention", "")
        for artifact in artifacts:
            if artifact.kind != "ENTITY_RESOLUTION":
                continue
            identity = artifact.structured_data.get("identity", {})
            entry = identity.get(mention)
            if entry:
                level = entry.get("level")
                if level == "CANONICAL" and entry.get("id"):
                    return RequirementAssessment(
                        requirement_id=requirement.id,
                        status=RequirementStatus.SATISFIED,
                        support_artifact_ids=[artifact.id],
                        supporting_exports=[e.id for e in artifact.exports],
                        note=f"{mention} -> {entry.get('name')} ({entry.get('id')}) [canonical]",
                    )
                return RequirementAssessment(
                    requirement_id=requirement.id,
                    status=RequirementStatus.SATISFIED,
                    support_artifact_ids=[artifact.id],
                    note=f"{mention} ~ {entry.get('name')} [candidate identity; sufficient for research, not a canonical id]",
                )
            for resolved in artifact.structured_data.get("resolved", []):
                if resolved.get("id") and (resolved.get("mention") == mention or mention in resolved.get("mention", "")):
                    return RequirementAssessment(
                        requirement_id=requirement.id,
                        status=RequirementStatus.SATISFIED,
                        support_artifact_ids=[artifact.id],
                        note=f"{mention} -> {resolved.get('name')} ({resolved.get('id')})",
                    )
            if artifact.structured_data.get("ambiguous"):
                return RequirementAssessment(
                    requirement_id=requirement.id,
                    status=RequirementStatus.PARTIAL,
                    support_artifact_ids=[artifact.id],
                    note="entity mention is ambiguous",
                )
        # No structured identity artifact; a grounded research artifact that names the
        # subject still establishes a RESOLVED (not canonical) identity.
        for artifact in self._grounded_artifacts(artifacts):
            if artifact.kind in {"ENTITY_RESOLUTION", 'DERIVED_MEASURE'}:
                continue  # a numeric derivation is not identity evidence
            if artifact.kind == 'DYNAMIC_FACT':
                ref = requirement.entity_ref
                ids = {s.subject_id for s in self._artifact_assessments[artifact.id].record_support}
                if not ref or not ref.identity_verified or str(ref.canonical_id) not in ids:
                    continue
            if self._artifact_names_subject(artifact, mention):
                return RequirementAssessment(
                    requirement_id=requirement.id,
                    status=RequirementStatus.SATISFIED,
                    support_artifact_ids=[artifact.id],
                    note=f"{mention} appears in grounded {artifact.kind} evidence (resolved by research)",
                )
        return RequirementAssessment(requirement_id=requirement.id, status=RequirementStatus.UNSATISFIED, note="entity was not resolved")

    def _scope_state(self, requirement, artifacts, channel: str) -> RequirementAssessment:
        # An empty population confirms nothing about the requested scope.
        grounded = [
            a for a in self._grounded_artifacts(artifacts)
            if not (a.kind == "DATABASE_RESULT" and a.structured_data.get("empty_population"))
        ]
        if not grounded:
            return RequirementAssessment(requirement_id=requirement.id, status=RequirementStatus.UNSATISFIED, note=f"no grounded artifact for {channel}")
        matches = [a for a in grounded if a.kind != 'DYNAMIC_FACT'
                   and self._artifact_matches_scope(a, requirement, channel)]
        matches.extend(a for a in grounded if a.kind == 'DERIVED_MEASURE'
                       and self._derived_scope_covers(a, requirement, channel)
                       and a not in matches)
        # A native current-state record proves only its inspected observation date,
        # never caller-supplied Artifact scope for a season/population/interval.
        value = requirement.value if isinstance(requirement.value, dict) else {}
        if channel == 'time' and value.get('start') == value.get('end') and value.get('start'):
            matches.extend(a for a in grounded if a.kind == 'DYNAMIC_FACT' and any(
                s.observed_at[:10] == value['start'] for s in self._artifact_assessments[a.id].record_support))
        if matches:
            return RequirementAssessment(
                requirement_id=requirement.id,
                status=RequirementStatus.SATISFIED,
                support_artifact_ids=[a.id for a in matches],
                note=f"{channel}={requirement.value} confirmed by {len(matches)} artifact(s)",
            )
        # A structured artifact exists but with a different scope: this is a partial, not a
        # failure. Non-structured research may still address the question.
        gaps = []
        for artifact in grounded:
            if artifact.kind != "DATABASE_RESULT":
                continue
            requested = Scope(season=requirement.value if channel == "season" else None,
                              game_population=str(requirement.value).upper() if channel == "population" else None)
            gaps.extend(compare_scope(requested, artifact.actual_scope))
        return RequirementAssessment(
            requirement_id=requirement.id,
            status=RequirementStatus.PARTIAL,
            support_artifact_ids=[a.id for a in grounded],
            gaps=gaps,
            note=f"grounded artifacts do not pin {channel}={requirement.value}",
        )

    def _derived_scope_covers(self, artifact: Artifact, requirement, channel: str) -> bool:
        """A validated derived computation pins the operand scopes it was computed from."""
        if artifact.kind != 'DERIVED_MEASURE':
            return False
        assessment = self._artifact_assessments.get(artifact.id)
        if assessment is None or assessment.status != 'ELIGIBLE':
            return False
        def root_scopes(proof):
            for operand in proof.get('roots', []):
                yield operand['scope']
        for source_scope in (scope for proof in assessment.derived_proofs for scope in root_scopes(proof)):
            if not isinstance(source_scope, dict):
                continue
            if channel == 'season' and source_scope.get('season') == requirement.value:
                return True
            if channel == 'population':
                from app.capabilities.analytics.catalog import CATALOG
                requested = CATALOG.resolve_population(str(requirement.value))
                if requested and set(source_scope.get('game_types') or []) == set(requested):
                    return True
            if channel == 'time' and isinstance(requirement.value, dict):
                window = source_scope.get('date_range') or {}
                if (window.get('start') == requirement.value.get('start')
                        and window.get('end') == requirement.value.get('end')):
                    return True
        return False

    def _artifact_matches_scope(self, artifact: Artifact, requirement, channel: str) -> bool:
        if artifact.kind == 'DERIVED_MEASURE':
            return self._derived_scope_covers(artifact, requirement, channel)
        scope = artifact.actual_scope
        if scope is None:
            return False
        if channel == "season":
            if scope.season == requirement.value: return True
            if artifact.kind == "DATABASE_RESULT" and receipt_usable(artifact):
                receipt = artifact.structured_data["execution_receipt"]
                requested = contract_for_goal(self._current_goal,self._current_artifacts,ExecutionAttestation.model_validate(receipt["attestation"]))
                executed = ExecutionAttestation.model_validate(receipt["attestation"])
                # The independent current-Goal admission has already checked exact time.
                return requested is not None and requested.season == requirement.value and window(requested.season,requested.date_range) == window(executed.season,executed.date_range)
            return False
        if channel == "population":
            return (scope.game_population or "").upper() == str(requirement.value).upper()
        if channel == "time":
            if artifact.kind == "DATABASE_RESULT" and receipt_usable(artifact):
                executed = ExecutionAttestation.model_validate(artifact.structured_data["execution_receipt"]["attestation"])
                requested = contract_for_goal(self._current_goal,self._current_artifacts,executed)
                return requested is not None and window(requested.season,requested.date_range) == window(executed.season,executed.date_range)
            return scope.date_range is not None
        return False

    def _analysis_state(self, requirement, artifacts, needs_by_id) -> RequirementAssessment:
        candidates = self._candidate_artifacts(requirement, artifacts, needs_by_id, kinds={"DATABASE_RESULT", "WEB_EVIDENCE", "KNOWLEDGE_ITEM", "DYNAMIC_FACT", "DERIVED_MEASURE"})
        propositions = [p for p in getattr(self, "_expected_propositions", {}).values()
                        if p.requirement_id == requirement.id]
        comparison_only = bool(propositions) and all(p.kind == PropositionKind.COMPARISON for p in propositions)
        # An explanation needs an explanation. A statistical artifact is a supporting
        # observation (PARTIAL), not by itself the requested explanation (ADR 0021).
        explanatory = self._completion(requirement) == RequirementCompletion.EXPLANATION
        predictive = bool(requirement.evidence_intent and requirement.evidence_intent.mode == 'PREDICTION')
        useful = []
        observations = []
        for artifact in candidates:
            if artifact.kind == "DATABASE_RESULT" and artifact.structured_data.get("row_count", 0) > 0 and not artifact.structured_data.get("empty_population"):
                (observations if explanatory or predictive else useful).append(artifact)
            elif artifact.kind == "DERIVED_MEASURE" and artifact.structured_data.get("row_count", 0) > 0:
                (observations if explanatory or predictive else useful).append(artifact)
            elif artifact.kind == "WEB_EVIDENCE" and any(
                self._finding_supported(f)
                for f in artifact.structured_data.get("findings", [])
            ):
                useful.append(artifact)
            elif artifact.kind == "KNOWLEDGE_ITEM":
                if self._knowledge_matches(artifact, requirement):
                    useful.append(artifact)
            elif artifact.kind == "DYNAMIC_FACT":
                useful.append(artifact)
        if comparison_only:
            # A statistical observation, computed row or prose finding is not itself
            # a governed comparative relation. Keep it partial, not satisfied.
            observations.extend(useful)
            useful = []
        if useful:
            return RequirementAssessment(
                requirement_id=requirement.id,
                status=RequirementStatus.SATISFIED,
                support_artifact_ids=[a.id for a in useful],
                note="grounded analysis available",
            )
        if observations or candidates:
            support = observations or candidates
            return RequirementAssessment(
                requirement_id=requirement.id,
                status=RequirementStatus.PARTIAL,
                support_artifact_ids=[a.id for a in support],
                note=(
                    "comparison operands and relation are not verified"
                    if comparison_only else
                    "historical observations are premises, not a verified forecast"
                    if predictive else
                    "a statistical observation supports the explanation but does not establish its cause"
                    if explanatory and observations
                    else "an analysis artifact exists but contains no material result"
                ),
            )
        return RequirementAssessment(requirement_id=requirement.id, status=RequirementStatus.UNSATISFIED, note="no analysis produced")

    def _measure_state(self, requirement, artifacts, needs_by_id) -> RequirementAssessment:
        """A requested measure is supported only if an accepted result actually computes it.

        Tool success, a row count or a column name are not evidence. The result's declared
        measures come from the validated Safe IR (see ``analytics.measures``).
        """
        value = requirement.value if isinstance(requirement.value, dict) else {}
        key = value.get("key")
        label = value.get("label") or requirement.statement
        if value.get("derivable") is False:
            return RequirementAssessment(
                requirement_id=requirement.id,
                status=RequirementStatus.UNSATISFIED,
                note=f"UNSUPPORTED_MEASURE: {label} is not derivable from the available data",
            )
        if not key:
            return RequirementAssessment(
                requirement_id=requirement.id,
                status=RequirementStatus.PARTIAL,
                note=f"the requested measure '{label}' could not be resolved to a canonical metric",
            )
        candidates = self._candidate_artifacts(requirement, artifacts, needs_by_id, kinds={"DATABASE_RESULT"})
        if not candidates:
            candidates = [
                artifact for artifact in artifacts
                if artifact.kind == "DATABASE_RESULT" and artifact.status in {"ACCEPTED", "PARTIAL"}
            ]
        declaring = [
            artifact for artifact in candidates
            if not artifact.structured_data.get("empty_population") and _artifact_declares(artifact, key)
        ]
        # A derived output does not inherit unrelated MEASURE authority from its
        # operands. It may satisfy only its own exact OperationContract Requirement.
        if declaring:
            return RequirementAssessment(
                requirement_id=requirement.id,
                status=RequirementStatus.SATISFIED,
                support_artifact_ids=[artifact.id for artifact in declaring],
                supporting_exports=[export.id for artifact in declaring for export in artifact.exports],
                note=f"MEASURE_OK: {label} ({key}) computed by {len(declaring)} artifact(s)",
            )
        if candidates:
            return RequirementAssessment(
                requirement_id=requirement.id,
                status=RequirementStatus.PARTIAL,
                support_artifact_ids=[artifact.id for artifact in candidates],
                note=f"MEASURE_MISMATCH: no accepted result computes {label} ({key})",
            )
        return RequirementAssessment(
            requirement_id=requirement.id,
            status=RequirementStatus.UNSATISFIED,
            note=f"MEASURE_MISMATCH: no result computes {label} ({key})",
        )

    def _evidence_state(self, requirement, artifacts, needs_by_id) -> RequirementAssessment:
        candidates = self._candidate_artifacts(requirement, artifacts, needs_by_id, kinds={"WEB_EVIDENCE", "KNOWLEDGE_ITEM", "DYNAMIC_FACT"})
        grounded = []
        for artifact in candidates:
            if artifact.kind == "WEB_EVIDENCE":
                relevant = [
                    f
                    for f in artifact.structured_data.get("findings", [])
                    if self._finding_supported(f)
                ]
                if relevant:
                    grounded.append(artifact)
            elif artifact.kind == "KNOWLEDGE_ITEM":
                if self._knowledge_matches(artifact, requirement):
                    grounded.append(artifact)
            else:
                grounded.append(artifact)
        if grounded:
            return RequirementAssessment(
                requirement_id=requirement.id,
                status=RequirementStatus.SATISFIED,
                support_artifact_ids=[a.id for a in grounded],
                note="grounded evidence available",
            )
        if candidates:
            return RequirementAssessment(
                requirement_id=requirement.id,
                status=RequirementStatus.PARTIAL,
                support_artifact_ids=[a.id for a in candidates],
                note="retrieved sources did not mention the subject of the question",
            )
        return RequirementAssessment(requirement_id=requirement.id, status=RequirementStatus.UNSATISFIED, note="no evidence produced")

    def _finding_is_relevant(self, finding: dict) -> bool:
        haystack = " ".join(
            [finding.get("title", ""), *(span.get("text", "") for span in finding.get("evidence_spans", []))]
        ).lower()
        subject_tokens = getattr(self, "_subject_tokens", set())
        if subject_tokens and not any(_mentions(haystack, token) for token in subject_tokens):
            return False
        # Question-relevance for a requested measure: the page must carry the measure.
        target_tokens = getattr(self, "_target_tokens", set())
        if target_tokens and not any(_mentions(haystack, token) for token in target_tokens):
            return False
        return True

    def _finding_supported(self, finding: dict) -> bool:
        """True when a finding is admitted as evidence.

        When proposition verification ran, only a span the verifier judged SUPPORTED for
        a proposition is admitted; subject overlap alone is not. Legacy findings without
        verification keep the Phase-4 subject/measure relevance rule (ADR 0022). A bounded
        Judge drill-down may upgrade a soft-ambiguous finding; it can never upgrade a hard
        conflict.
        """
        if "proposition_findings" in finding:
            proposition_findings = finding.get("proposition_findings") or []
            return any(
                self._item_status(item) == VerificationStatus.SUPPORTED.value
                for item in proposition_findings
            )
        return False

    def _item_status(self, item) -> str:
        if not self._accepted_nested_finding(item):
            return ""
        finding_id = str(item.get("id") or "")
        return self._resolved_findings.get(finding_id) or str(item.get("status") or "")

    def _effective_finding(self, finding: EvidenceFinding) -> EvidenceFinding:
        resolved = self._resolved_findings.get(finding.id)
        if resolved and resolved != finding.status.value:
            return finding.model_copy(update={"status": VerificationStatus(resolved)})
        return finding

    # ---------------------------------------------------------- source drill-down
    def _run_source_inspections(self, artifacts) -> None:
        """Bounded inspection of already-retrieved material for soft-ambiguous findings."""
        if self.max_inspections <= 0:
            return
        budget = self.max_inspections
        for finding in list(self._accepted_findings.values()):
            if budget <= 0:
                break
            trigger = _source_inspection.inspection_trigger(finding)
            if trigger is None:
                continue
            proposition = self._expected_propositions.get(finding.proposition_id)
            if proposition is None:
                continue
            inspection = _source_inspection.inspect_finding(finding, artifacts, level=2)
            if inspection is None:
                continue
            if inspection.context_chars == 0 and not inspection.representation_loaded:
                # Level 3: the already-retrieved representation itself, still bounded. Only
                # used when the neighboring-sentence level yielded nothing (for example a
                # structured span with no char offsets). No new fetch is performed.
                escalated = _source_inspection.inspect_finding(finding, artifacts, level=3)
                if escalated is not None:
                    inspection = escalated
            budget -= 1
            status, rationale, assessment = self._resolve_inspection(finding, proposition, inspection, artifacts)
            inspection.resolved_status = status
            inspection.rationale = rationale
            inspection.assessment = assessment
            self._inspections.append(inspection)
            # A bounded re-assessment may resolve an ambiguous finding to either support or a
            # decisive contradiction. Both are typed outcomes from the same compatibility gate;
            # an unresolved inspection stays out of the resolved map.
            if status in {VerificationStatus.SUPPORTED.value, VerificationStatus.CONTRADICTED.value}:
                self._resolved_findings[finding.id] = status

    def _resolve_inspection(self, finding, proposition, inspection, artifacts):
        """Re-assess a soft-ambiguous finding with neighboring, already-retrieved context."""
        # The finding already passed the deterministic hard gates. Drill-down only resolves
        # the typed temporal/assertion ambiguity; it cannot create relevance that the
        # proposition-relevance token gate did not already establish.
        if not finding.checks.get("target_ok", False):
            return (
                VerificationStatus.INSUFFICIENT.value,
                "the candidate passage does not carry the requested relationship",
                {},
            )
        document = self._document_for(finding, artifacts)
        enriched = " ".join(
            part for part in (inspection.before, inspection.span_text, inspection.after) if part
        )
        hint = _finite_contradiction_hint(proposition, enriched)
        fallback = deterministic_assessment(
            proposition, inspection.span_text, haystack=enriched,
            contradiction_hint=hint, document=document,
        )
        llm_assessment = request_assessment(
            self.llm, proposition, inspection.span_text, document,
            context_before=inspection.before, context_after=inspection.after,
            section=inspection.section,
        )
        if llm_assessment is not None:
            inspection.source = "llm"
        merged = merge_assessments(fallback, llm_assessment)
        grounding = validate_grounding_receipts(
            merged.grounding, span_text=inspection.span_text,
            context_before=inspection.before, context_after=inspection.after, document=document,
        )
        assessment, funded_escalations, _ = apply_grounding_policy(fallback, merged, grounding)
        temporal, assertion = evidence_expectations(proposition)
        compatibility = assess_compatibility(
            proposition, temporal, assertion, assessment, document, enriched
        )
        if compatibility.verdict == "COMPATIBLE" and assessment.source == "llm" and funded_escalations:
            decision = request_semantic_conformance(self.llm, proposition, assessment, funded_escalations)
            if decision is None or not decision.supported:
                assessment = downgrade_funded_escalations(fallback, assessment, funded_escalations)
                compatibility = assess_compatibility(
                    proposition, temporal, assertion, assessment, document, enriched
                )
        if compatibility.verdict == "COMPATIBLE":
            resolved = VerificationStatus.SUPPORTED.value
        elif compatibility.verdict == "CONTRADICTED":
            resolved = VerificationStatus.CONTRADICTED.value
        else:
            resolved = VerificationStatus.INSUFFICIENT.value
        return resolved, "; ".join(compatibility.reasons), assessment.model_dump()

    @staticmethod
    def _document_for(finding: EvidenceFinding, artifacts) -> RetrievedDocument | None:
        for artifact in artifacts:
            if getattr(artifact, "kind", "") != "WEB_EVIDENCE":
                continue
            data = artifact.structured_data or {}
            for raw in data.get("documents") or []:
                if isinstance(raw, dict) and str(raw.get("id")) == finding.document_id:
                    try:
                        return RetrievedDocument.model_validate(raw)
                    except Exception:  # noqa: BLE001 - a malformed document is not usable
                        return None
        return None

    def _accepted_nested_finding(self, item):
        canonical = self._accepted_findings.get(str(item.get("id") or ""))
        try:
            return canonical is not None and EvidenceFinding.model_validate(item) == canonical
        except Exception:
            return False

    def _knowledge_matches(self, artifact: Artifact, requirement) -> bool:
        """A retrieved knowledge entry must address the requirement, not just exist.

        Generic definitions successfully retrieved for another topic must not satisfy an
        analytical obligation (Phase 5B: retrieved != relevant).
        """
        # WSA-07/GA-08: only stable semantic knowledge may ground a Requirement. Mutable
        # factual items are cached evidence; they must not satisfy a current factual
        # obligation and are surfaced (if at all) through the qualified-evidence lane.
        items = [
            item for item in (artifact.structured_data.get("items", []) or [])
            if isinstance(item, dict) and _knowledge_class(str(item.get("type", ""))) == "SEMANTIC"
        ]
        if not items:
            return False
        texts = [artifact.text_content or ""]
        for item in items:
            texts.append(str(item.get("title", "")))
            texts.append(str(item.get("summary", "")))
            texts.extend(str(alias) for alias in item.get("aliases", []) or [])
        haystack = " ".join(texts).lower()
        subject_tokens = getattr(self, "_subject_tokens", set())
        if subject_tokens and not any(_mentions(haystack, token) for token in subject_tokens):
            return False
        objective = ""
        if isinstance(requirement.value, dict):
            objective = str(requirement.value.get("objective") or "")
        objective_text = f"{objective} {requirement.statement}"
        objective_tokens = _content_tokens_public(objective) | _content_tokens_public(requirement.statement)
        if objective_tokens:
            matched = {token for token in objective_tokens if _mentions(haystack, token)}
            if not matched:
                return False
            requested_years = set(re.findall(r"\b(?:19|20)\d{2}\b", objective_text))
            year_match = bool(requested_years) and any(year in haystack for year in requested_years)
            # A single incidental common word (e.g. "strike" appearing in a generic pitch
            # definition) must not satisfy a specific historical obligation. Require at
            # least two objective terms, or an explicit scope-year match.
            if len(matched) < min(2, len(objective_tokens)) and not year_match:
                return False
        return True

    def _entity_is_canonical(self, requirement, artifacts) -> bool:
        """True only when a verified canonical identity exists for the requirement.

        Completion (IDENTITY) is satisfied by a candidate/resolved name, but a database
        claim still requires a canonical id (ADR 0016/0013).
        """
        mention = (requirement.value or {}).get("mention", "") if isinstance(requirement.value, dict) else ""
        for artifact in artifacts:
            if artifact.kind != "ENTITY_RESOLUTION":
                continue
            entry = (artifact.structured_data.get("identity") or {}).get(mention)
            if entry and entry.get("level") == "CANONICAL" and entry.get("id"):
                return True
            for resolved in artifact.structured_data.get("resolved", []):
                if resolved.get("id") and (resolved.get("mention") == mention or mention in resolved.get("mention", "")):
                    return True
        return False

    def _artifact_names_subject(self, artifact: Artifact, mention: str) -> bool:
        if not mention:
            return False
        haystack = " ".join(
            [
                artifact.text_content or "",
                " ".join(str(v) for v in (artifact.actual_scope.entity_names if artifact.actual_scope else []) or []),
            ]
        ).lower()
        return mention.lower() in haystack

    def _constraint_state(self, requirement, artifacts, needs_by_id) -> RequirementAssessment:
        value = requirement.value if isinstance(requirement.value, dict) else {}
        if "assertion" in value:
            dynamic = self._candidate_artifacts(requirement, artifacts, needs_by_id, kinds={'DYNAMIC_FACT'})
            if dynamic:
                return RequirementAssessment(requirement_id=requirement.id, status=RequirementStatus.SATISFIED, support_artifact_ids=[a.id for a in dynamic], note="assertion checked against a provider")
            return RequirementAssessment(requirement_id=requirement.id, status=RequirementStatus.PARTIAL, note="user assertion remains UNVERIFIED")
        analytics = [a for a in artifacts if a.kind == "DATABASE_RESULT" and a.status in {"ACCEPTED", "PARTIAL"}]
        proven = []
        for artifact in analytics:
            executed = artifact.structured_data["execution_receipt"]["attestation"]
            role = executed.get("analytical_role")
            measure_context = " ".join(m.get("key", "").replace("_", " ") for m in executed["measures"] if m.get("verified"))
            obligation = constraint_obligations(requirement.value, role, measure_context)
            if not obligation: continue
            ok = True
            for field,wanted in obligation.items():
                if field in {"ordering", "ordering_tail", "tie_break_ordering"}:
                    actual = [dict(metric=o.get("metric") or o["column"],direction=o["direction"]) for o in executed["ordering"]]
                    wanted = [dict(metric=o.get("metric") or o.get("field"),direction=o["direction"]) for o in wanted]
                    ok &= (actual[1:1+len(wanted)] if field in {"ordering_tail", "tie_break_ordering"} else actual[:len(wanted)]) == wanted
                elif field == "filters":
                    # Full population equality was already checked at receipt admission.
                    ok &= set(canonical_population(wanted)) <= set(canonical_population(executed["filters"]))
                elif field == "group_by":
                    ok &= set(wanted) <= set(executed["group_by"])
                else:
                    ok &= executed.get(field) == wanted
            if ok: proven.append(artifact)
        analytics = proven
        if analytics:
            return RequirementAssessment(requirement_id=requirement.id, status=RequirementStatus.SATISFIED, support_artifact_ids=[a.id for a in analytics], note="qualification attested by execution")
        return RequirementAssessment(requirement_id=requirement.id, status=RequirementStatus.UNSATISFIED, note="constraint could not be applied")

    # ------------------------------------------------------------------ background
    def _background_allowed(self, goal: Goal) -> bool:
        # A requested value is never answered from background.
        if any(r.kind == RequirementKind.MEASURE and r.required for r in goal.requirements):
            return False
        return any(
            r.required and self._completion(r) == RequirementCompletion.EXPLANATION and r.background_ok
            for r in goal.requirements
        )

    # ------------------------------------------------------------------ claims
    def _claims(self, goal, artifacts, needs_by_id, states, local_support=None) -> list[Claim]:
        local_support = local_support or {}
        supported_ids = {aid for state in states if state.status in {RequirementStatus.SATISFIED, RequirementStatus.PARTIAL} for aid in state.support_artifact_ids}

        entity_requirements = [r for r in goal.requirements if r.kind == RequirementKind.ENTITY and r.required]
        # A curated definition is direct evidence for a definition question. Web pages that
        # merely use the term are already excluded by proposition verification
        # (``_finding_supported`` requires an accepted proposition finding), so no separate
        # definition-gate is needed. The earlier gate also suppressed genuine
        # proposition-verified web evidence for explanatory questions whenever a knowledge
        # item happened to be retrieved, which left the Judge marking a proposition SATISFIED
        # with no claim the Composer could communicate (M2.6 direct composition defect).
        # A database claim may only be asserted when the subject identity was actually
        # established. This gate affects database claims only; independent web/knowledge
        # claims are admitted on their own merits (ADR 0017).
        entity_canonical = all(
            self._entity_is_canonical(r, artifacts)
            for r in entity_requirements
        )
        measure_requirements = [r for r in goal.requirements if r.kind == RequirementKind.MEASURE and r.required]
        # Requirement-local measure gate: an Artifact only has to declare the measures
        # owned by the Requirements it independently conforms to. A sibling unsupported
        # measure no longer erases an independently supported analysis claim.
        measures_by_id = {r.id: r for r in measure_requirements}

        # An unverified comparison-only observation may be retained diagnostically,
        # but it must not be a factual claim or a public table. Keep an independently
        # supported VALUE/GROUNDED/etc. result from the same Artifact.
        propositions = ensure_propositions(goal)
        comparison_requested = any(p.kind == PropositionKind.COMPARISON for p in propositions)
        factual_requirements = {p.requirement_id for p in propositions if p.kind not in {
            PropositionKind.COMPARISON, PropositionKind.IDENTITY, PropositionKind.SCOPE
        }}
        noncomparison_support = {
            aid for state in states if state.requirement_id in factual_requirements
            for aid in state.support_artifact_ids
        }
        claims: list[Claim] = []
        seen_web: set[str] = set()
        for artifact in artifacts:
            if artifact.id not in supported_ids or artifact.status == "REJECTED":
                continue
            if comparison_requested and artifact.kind in _GROUNDED_KINDS and artifact.id not in noncomparison_support:
                continue
            if artifact.kind == "DATABASE_RESULT":
                subject_proofs = self._artifact_assessments[artifact.id].subject_proofs
                if subject_proofs:
                    # Publish only inspected subject cells, never the entire raw table
                    # or Tool prose (which can include an unresolved/conflicting sibling).
                    for proof in subject_proofs:
                        claims.append(Claim(
                            text=f"{proof['subject']}: " + '; '.join(
                                f"{c['metric']}={c['value']} {c['unit']}" +
                                (f" {c['dimensions']}" if c['dimensions'] else '') for c in proof['cells']) +
                                f"; season={proof['requested']['season']}; population={proof['requested']['game_types']}",
                            support_refs=[Reference(ref_type=ReferenceType.ARTIFACT,
                                ref_id=artifact.id, source='subject_cells')],
                            requirement_ids=[proof['requirement_id']], proposition_ids=[proof['proposition_id']],
                            scope=artifact.actual_scope.model_copy(update={'entities': proof['subject_ids'],
                                'entity_names': [proof['subject']], 'measures': sorted({c['metric'] for c in proof['cells']})}),
                            epistemic=EpistemicStatus.SUPPORTED,
                            answer_values=[dict(requirement_id=proof['requirement_id'], metric=c['metric'],
                                value=c['value'], unit=c['unit'], entity=proof['subject'],
                                season=proof['requested']['season'], population=proof['requested']['game_types'])
                                for c in proof['cells']] ))
                proofs = self._artifact_assessments[artifact.id].reporting_proofs
                for proof in proofs:
                    scope = proof['requested']
                    subjects = ', '.join(scope['subjects']) or 'league total'
                    claims.append(Claim(
                        text=(f"{subjects}: {scope['measure']} = {proof['value']}; "
                              f"season={scope['season']}; window={scope['date_range']}; "
                              f"population={scope['game_types']}; role={scope['analytical_role']}; "
                              f"qualification={scope['qualification']}"),
                        support_refs=[Reference(ref_type=ReferenceType.ARTIFACT,
                            ref_id=artifact.id, source='reporting_cell')],
                        requirement_ids=[proof['requirement_id']], proposition_ids=[proof['proposition_id']],
                        scope=artifact.actual_scope, epistemic=EpistemicStatus.SUPPORTED,
                        answer_values=[{'requirement_id': proof['requirement_id'],
                            'metric': scope['measure'], 'value': proof['value'], 'entity': subjects,
                            'season': scope['season'], 'population': scope['game_types']}]))
                legacy_owners = {p.requirement_id for p in propositions if p.reporting_index is None
                                 and p.subject_granularity is None
                                 and p.kind not in {PropositionKind.IDENTITY, PropositionKind.SCOPE,
                                                    PropositionKind.COMPARISON}}
                if (proofs or subject_proofs or any(p.reporting_index is not None or p.subject_granularity is not None
                                                   for p in propositions)) and not (
                        local_support.get(artifact.id, set()) & legacy_owners):
                    continue  # neither a cell nor scope-only support blesses unrelated rows/tool prose
                # Entity gate: no canonical id -> no database claim.
                if entity_requirements and not entity_canonical:
                    continue
                # Measure gate: this artifact must declare the measure(s) owned by the
                # Requirements it was admitted to serve. The gate is per-artifact and
                # Requirement-local, so an unsupported sibling measure cannot erase it.
                artifact_measure_keys = {
                    measure.value.get("key")
                    for requirement_id in local_support.get(artifact.id, set())
                    if (measure := measures_by_id.get(requirement_id)) is not None
                    and isinstance(measure.value, dict) and measure.value.get("key")
                }
                if artifact_measure_keys and not any(
                    _artifact_declares(artifact, key) for key in artifact_measure_keys
                ):
                    continue
                rows = artifact.structured_data.get("rows", [])
                if not rows:
                    continue
                claims.append(
                    Claim(
                        text=f"Statcast analysis ({artifact.structured_data.get('intent_summary') or artifact.need_id}):\n{artifact.text_content[:6000]}",
                        support_refs=[
                            Reference(ref_type=ReferenceType.ARTIFACT, ref_id=artifact.id, source="artifact"),
                            *[r for r in artifact.references if r.ref_type == ReferenceType.SQL],
                        ],
                        confidence=artifact.confidence,
                        scope=artifact.actual_scope,
                        epistemic=EpistemicStatus.SUPPORTED,
                        answer_values=_answer_values(artifact, measure_requirements),
                    )
                )
            elif artifact.kind == "WEB_EVIDENCE":
                for finding in artifact.structured_data.get("findings", []):
                    if not self._finding_supported(finding):
                        continue
                    accepted = [item for item in finding.get("proposition_findings", [])
                                if self._item_status(item) == "SUPPORTED"
                                and self._accepted_nested_finding(item)
                                and self._finding_owners.get(item.get("id")) == artifact.id
                                and (p := self._expected_propositions.get(item.get("proposition_id"))) is not None
                                and p.kind != PropositionKind.COMPARISON]
                    if not accepted:
                        continue
                    accepted_ids = {item.get("span_id") for item in accepted}
                    spans = " ".join(span.get("text", "") for span in artifact.structured_data.get("evidence_spans", [])
                                     if span.get("id") in accepted_ids)
                    key = f"{finding.get('url', '')}|{spans[:120]}"
                    if key in seen_web:
                        continue
                    seen_web.add(key)
                    claims.append(
                        Claim(
                            text=f"{finding.get('title')} ({finding.get('url')}, retrieved {finding.get('retrieved_at')}): {spans}",
                            support_refs=[Reference(ref_type=ReferenceType.WEB_EVIDENCE, ref_id=finding.get("url", ""), source=finding.get("url", ""), span=spans)],
                            confidence="ACCEPTABLE",
                            epistemic=EpistemicStatus.SUPPORTED,
                            proposition_ids=sorted({str(item["proposition_id"]) for item in accepted if item.get("proposition_id")}),
                            verification=VerificationStatus.SUPPORTED,
                        )
                    )
            elif artifact.kind == "DERIVED_MEASURE":
                rows = artifact.structured_data.get("rows", [])
                assessment = self._artifact_assessments[artifact.id]
                if not rows or not assessment.eligible_proposition_ids:
                    continue  # no validated operand lineage -> not a factual Claim
                claims.append(
                    Claim(
                        text="Computed result:\n" + "\n".join(
                            f"{proof['operation']['kind'].lower()} result: {proof['value']}"
                            for proof in assessment.derived_proofs),
                        support_refs=[Reference(ref_type=ReferenceType.ARTIFACT, ref_id=artifact.id, source="derived_computation")],
                        confidence=artifact.confidence,
                        scope=artifact.actual_scope,
                        epistemic=EpistemicStatus.SUPPORTED,
                        proposition_ids=list(assessment.eligible_proposition_ids),
                    )
                )
            elif artifact.kind == "DYNAMIC_FACT":
                assessment = self._artifact_assessments[artifact.id]
                records = {s.record_index for s in assessment.record_support}
                for index in sorted(records):
                    fact = artifact.structured_data['facts'][index]
                    bindings = [s for s in assessment.record_support if s.record_index == index]
                    claims.append(Claim(
                        text=f"Dynamic fact: {fact['player_name']} — TEAM_MEMBERSHIP = {fact['value']} (as of {fact['as_of']})",
                        support_refs=[Reference(ref_type=ReferenceType.ARTIFACT, ref_id=artifact.id, source='structured_provider'),
                            Reference(ref_type=ReferenceType.WEB_EVIDENCE, ref_id=fact['source_url'],
                                      source=fact['source_url'], span=fact['value'],
                                      metadata={'locator': bindings[0].locator, 'response_hash': bindings[0].response_hash})],
                        confidence='STRONG', epistemic=EpistemicStatus.SUPPORTED,
                        proposition_ids=sorted({s.proposition_id for s in bindings}),
                    ))
            elif artifact.kind == "KNOWLEDGE_ITEM":
                for item in artifact.structured_data.get("items", [])[:2]:
                    if _knowledge_class(str(item.get("type", ""))) != "SEMANTIC":
                        continue  # mutable factual state is not a knowledge claim (WSA-07)
                    # Structural ownership must not manufacture semantic relevance.
                    # Reuse the existing Requirement relevance gate, independently for
                    # this item: neither partial artifact support nor a relevant sibling
                    # licenses another knowledge entry as a factual Claim.
                    item_view = artifact.model_copy(update={
                        'structured_data': {'items': [item]},
                        'text_content': f"{item.get('title', '')}: {item.get('summary', '')}",
                    })
                    candidates = {s.requirement_id for s in states if artifact.id in s.support_artifact_ids}
                    owners = {r.id for r in self._current_goal.requirements if r.id in candidates
                              and self._knowledge_matches(item_view, r)}
                    proposition_ids = [p.id for p in propositions if p.requirement_id in owners
                                       and p.requirement_id in self._authoritative_ids
                                       and p.kind != PropositionKind.COMPARISON]
                    if not proposition_ids:
                        continue
                    claims.append(
                        Claim(
                            text=f"{item.get('title')}: {item.get('summary')}",
                            proposition_ids=proposition_ids,
                            support_refs=[Reference(ref_type=ReferenceType.KNOWLEDGE_ITEM, ref_id=item.get("id", ""), source="knowledge", span=item.get("summary", ""))],
                            confidence="STRONG",
                            epistemic=EpistemicStatus.SUPPORTED,
                        )
                    )
        return claims

    def _limitations(self, goal, states) -> list[str]:
        notes = []
        by_id = {r.id: r for r in goal.requirements}
        for state in states:
            requirement = by_id.get(state.requirement_id)
            if requirement is None or not requirement.required:
                continue
            if state.status == RequirementStatus.SATISFIED:
                continue
            label = f"{requirement.kind.value}: {requirement.statement}"
            if state.status == RequirementStatus.PARTIAL:
                notes.append(f"partial coverage — {label} ({state.note})")
            elif state.status == RequirementStatus.BLOCKED:
                notes.append(f"blocked — {label} ({state.note})")
            else:
                notes.append(f"not covered — {label} ({state.note})")
        return notes


def _state_to_coverage(state: RequirementAssessment | None) -> CoverageState:
    """Refine a coarse RequirementAssessment into proposition coverage.

    Proposition coverage is a refinement of the existing requirement assessment, not a
    second completion system: the mapping is total and one-way.
    """
    if state is None:
        return CoverageState.NOT_ATTEMPTED
    if state.status == RequirementStatus.SATISFIED:
        return CoverageState.SATISFIED
    if state.status == RequirementStatus.PARTIAL:
        return CoverageState.PARTIAL
    if state.status == RequirementStatus.BLOCKED:
        return CoverageState.BLOCKED
    return CoverageState.UNSUPPORTED


def _qualified_integrity(finding: EvidenceFinding, span: dict, document: dict) -> bool:
    """Span/document provenance integrity for a qualified (non-asserted) presentation."""
    try:
        canonical_span = EvidenceSpan.model_validate(span)
        canonical_document = RetrievedDocument.model_validate(document)
    except Exception:  # noqa: BLE001 - malformed provenance is never shown
        return False
    return (
        canonical_span.document_id == finding.document_id
        and span_resolves(canonical_span, canonical_document)
        and (not finding.span_hash or finding.span_hash == text_hash(canonical_span.text))
        and (not finding.source_url or finding.source_url in {canonical_document.url, canonical_document.final_url})
        and (not finding.content_hash or finding.content_hash == canonical_document.content_hash)
    )


def _qualification(finding: EvidenceFinding) -> tuple[str, str | None]:
    """Classify why a relevant finding is not approved and phrase its limitation."""
    reason = next((r for r in finding.semantic_reasons if r), "")
    if finding.status == VerificationStatus.CONTRADICTED:
        return "CONTRADICTED", reason or "the evidence contradicts the requested statement"
    if finding.checks.get("authority_compatible") is False:
        return "AUTHORITY", reason or "the source role is not sufficient for the requested authority"
    if finding.checks.get("temporal_compatible") is False:
        return "TEMPORAL", reason or "the evidence does not establish the requested time"
    if finding.checks.get("assertion_compatible") is False:
        return "ASSERTION", reason or "the evidence is not asserted strongly enough"
    if finding.semantic_compatibility == "INSUFFICIENT":
        return "SEMANTIC", reason or "the evidence does not establish the requested statement"
    return "SEMANTIC", reason or None


def _coverage_from_findings(findings: list[EvidenceFinding]) -> CoverageState:
    statuses = {
        finding.status
        for finding in findings
        # A support decision that rests on an unresolved or hard-conflicting record is not
        # admissible coverage (Phase 5B closure, ADR 0022/0023).
        if not (finding.hard_conflict and finding.status == VerificationStatus.SUPPORTED)
    }
    if VerificationStatus.SUPPORTED in statuses and VerificationStatus.CONTRADICTED in statuses:
        return CoverageState.PARTIAL
    if VerificationStatus.CONTRADICTED in statuses:
        return CoverageState.CONTRADICTED
    if VerificationStatus.SUPPORTED in statuses:
        return CoverageState.SATISFIED
    return CoverageState.UNSUPPORTED


def _finding_resolves(finding: EvidenceFinding, span_by_id: dict, documents: dict) -> bool:
    """Referential integrity: finding -> persisted span -> OK document, offsets consistent."""
    if not finding.admissible or finding.hard_conflict or finding.hard_reasons:
        return False
    if any(finding.checks.get(key) in {"MISMATCH", "UNKNOWN"} for key in ("time_state", "population_state")):
        return False
    try:
        span = EvidenceSpan.model_validate(span_by_id.get(finding.span_id))
        document = RetrievedDocument.model_validate(documents.get(finding.document_id))
    except Exception:
        return False
    return (span.document_id == finding.document_id and span_resolves(span, document)
            and finding.span_hash == text_hash(span.text)
            and finding.source_url in {document.url, document.final_url}
            and finding.content_hash == document.content_hash and finding.retrieved_at == document.retrieved_at)


def _coverage_note(proposition: AnswerProposition, state: RequirementAssessment | None) -> str:
    if state is not None and state.note:
        return state.note
    return proposition.note


def _answer_values(artifact, requirements):
    """Only user-required measures, never auxiliary columns or identity numbers."""
    values = []
    scope = artifact.actual_scope
    for requirement in requirements:
        key = requirement.value.get("key") if isinstance(requirement.value, dict) else None
        for measure in artifact.structured_data.get("actual_measures", []):
            if measure.get("key") != key or not measure.get("verified"):
                continue
            column = measure.get("column")
            for row in artifact.structured_data.get("rows", []):
                if row.get(column) is None:
                    continue
                values.append({"requirement_id": requirement.id, "metric": key, "value": row[column],
                               "entity": row.get("player_name") or row.get("name") or "",
                               "season": scope.season if scope else None,
                               "population": scope.game_population if scope else None,
                               "unit": measure.get("unit")})
    return values


def _artifact_declares(artifact: Artifact, key: str) -> bool:
    """True when an artifact's own declared measures include ``key``.

    Two sources are trusted together: the machine-checked ``actual_measures`` block built
    from the Safe IR, and the artifact's ``actual_scope.measures``.
    """
    if not key:
        return False
    if artifact.kind == "DATABASE_RESULT" and not receipt_usable(artifact):
        return False
    scope = artifact.actual_scope
    if scope is not None and key in set(scope.measures or []):
        return True
    for item in artifact.structured_data.get("actual_measures", []) or []:
        if isinstance(item, dict) and item.get("key") == key and item.get("verified"):
            return True
    return False


def _tokens(text: str) -> set[str]:
    """Word tokens that also split Latin/CJK script boundaries.

    Combined scripts ("解释OPS") must yield both "解释" and "ops" so a Chinese question
    about an English concept can still be matched against English pages and vice versa.
    """
    tokens: set[str] = set()
    for match in re.finditer(r"[0-9a-z%]+|[\u4e00-\u9fff]+", (text or "").lower()):
        token = match.group(0)
        if len(token) >= 3 or (len(token) >= 2 and "\u4e00" <= token[0] <= "\u9fff"):
            tokens.add(token)
    return tokens


def _mentions(haystack: str, token: str) -> bool:
    """Whole-word match for ASCII tokens to avoid "ops" matching "operations"."""
    if not token:
        return False
    if all(ord(ch) < 128 for ch in token):
        return re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", haystack) is not None
    return token in haystack
