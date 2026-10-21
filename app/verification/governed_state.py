"""Whole-state integrity checks. This module never re-Judges evidence."""
import json
from collections import Counter
from dataclasses import dataclass

from app.contracts.models import (
    AnswerProjection, CoverageState, GoalStatus, EpistemicStatus,
    RequirementAssessment, RequirementAuthority,
)
from app.response.accepted_results import COMMUNICABLE_KINDS
from app.verification.coverage import requirement_coverage, status_for_coverage, goal_status


class GovernedStateError(RuntimeError):
    """An upstream governor must evaluate again; presentation cannot repair authority."""

    def __init__(self, message, *, projection=None):
        super().__init__(message)
        self.projection = projection  # invalid sealed candidate, never public authority


@dataclass(frozen=True)
class ExpectedObligations:
    """Detached controller-owned source graph, never supplied by a candidate."""
    goal_id: str
    requirement_ids: tuple[str, ...]
    propositions_json: str
    mandatory_requirement_ids: tuple[str, ...] = ()

    @classmethod
    def from_goal(cls, goal):
        from app.verification.propositions import ensure_propositions
        return cls(goal.id, tuple(r.id for r in goal.requirements), json.dumps(
            [p.model_dump(mode='json') for p in ensure_propositions(goal)]),
            tuple(r.id for r in goal.requirements if r.required))

    def propositions(self):
        return json.loads(self.propositions_json)


_EVIDENCE_STATE_FIELDS = frozenset({
    'coverage', 'supporting_claim_ids', 'contradicting_claim_ids',
    'supporting_artifact_ids', 'evidence_finding_ids', 'gaps', 'note',
})


def _obligation_meaning(data):
    return {key: value for key, value in data.items() if key not in _EVIDENCE_STATE_FIELDS}


def reaggregate_assessments(projection):
    """Canonical child aggregation shared by Judge finalization and fresh recovery."""
    existing = {a.requirement_id:a for a in projection.requirement_assessments}
    ids = set(existing) | {rid for p in projection.propositions for rid in p.owner_requirement_ids}
    result = []
    for rid in sorted(ids):
        children = [p for p in projection.propositions if rid in p.owner_requirement_ids]
        coverage = requirement_coverage(children)
        original = existing.get(rid)
        assessment = RequirementAssessment.model_validate(original.model_dump(mode='json')) if original else RequirementAssessment(
            requirement_id=rid,status=status_for_coverage(coverage))
        assessment.coverage = coverage
        assessment.status = status_for_coverage(coverage)
        assessment.support_artifact_ids = sorted({aid for p in children for aid in p.supporting_artifact_ids})
        assessment.support_refs = [ref for c in projection.claims
            if any(p.id in c.proposition_ids for p in children) for ref in c.support_refs]
        assessment.coverage_gaps = list(dict.fromkeys(gap for p in children for gap in p.gaps))
        assessment.conflicts = list(dict.fromkeys(cid for p in children for cid in p.contradicting_claim_ids))
        # Exports/old evidence refs cannot independently retain satisfaction.
        if coverage not in {CoverageState.SATISFIED,CoverageState.PARTIAL}:
            assessment.supporting_exports = []
        result.append(assessment)
    return result


def _subject_claim_valid(claim, propositions, assessments):
    """Cross-reference consistency only; evidence admission remains Judge-owned."""
    for pid in claim.get('proposition_ids', []):
        prop = propositions.get(pid, {})
        grain = prop.get('subject_granularity')
        if grain is None:
            continue
        if grain != 'PER_SUBJECT':
            return False
        proofs = [proof for ref in claim.get('support_refs', [])
                  if ref.get('ref_type') == 'ARTIFACT' and ref.get('source') == 'subject_cells'
                  for proof in assessments.get(ref.get('ref_id'), {}).get('subject_proofs', [])
                  if proof.get('proposition_id') == pid and proof.get('artifact_id') == ref.get('ref_id')]
        if not any(proof.get('requirement_id') == prop.get('requirement_id')
                   and proof.get('subject') == prop.get('subject')
                   and sorted(proof.get('subject_ids', [])) == sorted((claim.get('scope') or {}).get('entities', []))
                   and len(proof.get('subject_ids', [])) == 1
                   and proof.get('row_indices') and proof.get('fields') for proof in proofs):
            return False
    return True


def validate_governed_projection(projection, *, expected=None):
    if expected is not None:
        source = {p['id']: p for p in expected.propositions()}
        if projection.goal_id != expected.goal_id:
            raise GovernedStateError('projection belongs to a different source Goal')
        if {a.requirement_id for a in projection.requirement_assessments} != set(expected.requirement_ids):
            raise GovernedStateError('Requirement set differs from controller-owned source Goal')
        if {p.id for p in projection.propositions} != set(source):
            raise GovernedStateError('proposition set differs from controller-owned source Goal')
        for prop in projection.propositions:
            original = source[prop.id]
            if _obligation_meaning(prop.model_dump(mode='json')) != _obligation_meaning(original):
                raise GovernedStateError('source obligation meaning/ownership/mandatory/dependency graph changed')
    props = {p.id:p for p in projection.propositions}
    claims = {c.id:c for c in projection.claims}
    assessments = {a.requirement_id:a for a in projection.requirement_assessments}
    artifacts = {a.artifact_id:a for a in projection.artifact_assessments}
    if len(artifacts) != len(projection.artifact_assessments):
        raise GovernedStateError('duplicate Artifact assessment')
    if len(props) != len(projection.propositions) or len(claims) != len(projection.claims):
        raise GovernedStateError('duplicate proposition/Claim identity')
    if len(assessments) != len(projection.requirement_assessments):
        raise GovernedStateError('duplicate Requirement assessment')
    if (any(not p.owner_requirement_ids for p in props.values())
            or not {rid for p in props.values() for rid in p.owner_requirement_ids} <= set(assessments)):
        raise GovernedStateError('missing Requirement assessment')
    for aid in projection.supporting_artifact_ids:
        if aid not in artifacts:
            raise GovernedStateError('dangling governed Artifact reference')
    subject_props = {p.id: p.model_dump(mode='json') for p in props.values() if p.subject_granularity is not None}
    subject_assessments = {aid: a.model_dump(mode='json') for aid, a in artifacts.items() if a.subject_proofs}
    for claim in claims.values():
        if subject_props and not _subject_claim_valid(claim.model_dump(mode='json'), subject_props, subject_assessments):
            raise GovernedStateError('Claim subject footprint differs from its approved individual evidence unit')
        if any(ref.ref_type.value == 'ARTIFACT' and ref.ref_id not in artifacts for ref in claim.support_refs):
            raise GovernedStateError('Claim references an absent Artifact assessment')
        if any(pid not in props for pid in claim.proposition_ids):
            raise GovernedStateError('Claim references an absent proposition')
        if claim.epistemic == EpistemicStatus.SUPPORTED and (not claim.support_refs or not claim.proposition_ids):
            raise GovernedStateError('SUPPORTED Claim lacks evidence references or obligation ownership')
        if claim.epistemic == EpistemicStatus.SUPPORTED and any(
                rid not in assessments or
                assessments[rid].authority_status != RequirementAuthority.AUTHORITATIVE
                for pid in claim.proposition_ids for rid in props[pid].owner_requirement_ids):
            raise GovernedStateError('SUPPORTED Claim belongs to an unproved Requirement')
    visiting, visited = set(), set()
    def dependency(pid):
        if pid not in props or pid in visiting:
            raise GovernedStateError('missing/cyclic proposition dependency')
        if pid in visited:
            return
        visiting.add(pid)
        for parent in props[pid].depends_on:
            dependency(parent)
        visiting.remove(pid)
        visited.add(pid)
    for pid in props:
        dependency(pid)
    for proposition in props.values():
        if any(aid not in artifacts for aid in proposition.supporting_artifact_ids):
            raise GovernedStateError('proposition references an absent Artifact assessment')
        if proposition.coverage == CoverageState.SATISFIED and not (
                proposition.supporting_claim_ids or proposition.supporting_artifact_ids or proposition.evidence_finding_ids):
            raise GovernedStateError('satisfied proposition has no inspectable support')
        for cid in proposition.supporting_claim_ids:
            if cid not in claims or proposition.id not in claims[cid].proposition_ids:
                raise GovernedStateError('dangling/nonreciprocal proposition Claim reference')
        if (proposition.mandatory and proposition.coverage == CoverageState.SATISFIED
                and proposition.kind in COMMUNICABLE_KINDS
                and not any(proposition.id in claim.proposition_ids
                            and claim.epistemic == EpistemicStatus.SUPPORTED
                            and claim.support_refs for claim in claims.values())):
            raise GovernedStateError('satisfied communicable proposition has no approved Claim')
    for rid, assessment in assessments.items():
        coverage = requirement_coverage([p for p in props.values() if rid in p.owner_requirement_ids])
        if assessment.coverage != coverage or assessment.status != status_for_coverage(coverage):
            raise GovernedStateError('Requirement assessment disagrees with mandatory child aggregation')
        if coverage == CoverageState.SATISFIED and assessment.authority_status != RequirementAuthority.AUTHORITATIVE:
            raise GovernedStateError('unproved Requirement cannot be satisfied')
    canonical = {a.requirement_id:a for a in reaggregate_assessments(projection)}
    for rid, assessment in assessments.items():
        for field in ('support_artifact_ids','support_refs','coverage_gaps','conflicts'):
            if getattr(assessment,field) != getattr(canonical[rid],field):
                raise GovernedStateError('stale Requirement support/gap/conflict aggregation')
    mandatory_ids = (set(expected.mandatory_requirement_ids) if expected is not None else
                     {rid for p in props.values() if p.mandatory for rid in p.owner_requirement_ids})
    if projection.goal_status == GoalStatus.COMPLETE and any(
            rid not in assessments or assessments[rid].coverage != CoverageState.SATISFIED
            for rid in mandatory_ids):
        raise GovernedStateError('COMPLETE has an unsatisfied mandatory Requirement')
    expected = goal_status(projection.propositions,clarification=projection.clarification,
                           background_ok=projection.background_ok)
    if projection.goal_status == GoalStatus.COMPLETE and expected != GoalStatus.COMPLETE:
        raise GovernedStateError('COMPLETE disagrees with mandatory proposition coverage')
    if expected == GoalStatus.COMPLETE and projection.goal_status != GoalStatus.COMPLETE:
        raise GovernedStateError('all mandatory propositions satisfied but status is not COMPLETE')
    if projection.goal_status == GoalStatus.FAILED and (expected != GoalStatus.FAILED or projection.claims):
        raise GovernedStateError('FAILED cannot discard material approved support')
    if (projection.goal_status == GoalStatus.WAITING_FOR_USER) != (projection.clarification is not None):
        raise GovernedStateError('clarification and waiting status disagree')
    return projection


def governed_limited_fallback(projection, *, expected=None):
    """New demoted snapshot; never invent/promote Claims or mutate the source."""
    data = projection.model_dump(mode='json')
    reset_propositions = set()
    if expected is not None:
        originals = {p['id']: p for p in expected.propositions()}
        counts = Counter(p['id'] for p in data['propositions'])
        present = {p['id']: p for p in data['propositions'] if counts[p['id']] == 1}
        restored = []
        for pid, original in originals.items():
            prop = present.get(pid)
            if prop is None or _obligation_meaning(prop) != _obligation_meaning(original):
                reset_propositions.add(pid)
                prop = original
                prop.update(coverage='UNSUPPORTED', supporting_claim_ids=[], supporting_artifact_ids=[],
                            evidence_finding_ids=[], contradicting_claim_ids=[],
                            gaps=['integrity recovery: restored missing/changed source obligation'])
            restored.append(prop)
        data['goal_id'] = expected.goal_id
        data['propositions'] = restored
        counts = Counter(a['requirement_id'] for a in data['requirement_assessments'])
        known = {a['requirement_id']: a for a in data['requirement_assessments']
                 if counts[a['requirement_id']] == 1}
        data['requirement_assessments'] = [known[rid] if rid in known else
            RequirementAssessment(requirement_id=rid,status='UNSATISFIED').model_dump(mode='json')
            for rid in expected.requirement_ids]
    ids = {p['id'] for p in data['propositions']}
    artifact_ids = {a['artifact_id'] for a in data['artifact_assessments']}
    data['supporting_artifact_ids'] = [aid for aid in data['supporting_artifact_ids'] if aid in artifact_ids]
    # Invalid references are not approved facts. Keep every intact approved Claim.
    owner_assessments = {a['requirement_id']:a for a in data['requirement_assessments']}
    subject_props = {p['id']: p for p in data['propositions'] if p.get('subject_granularity') is not None}
    subject_assessments = {a['artifact_id']: a for a in data['artifact_assessments']}
    prop_owners = {p['id']: list(dict.fromkeys([p['requirement_id'], *p.get('requirement_ids', [])]))
                   for p in data['propositions']}
    data['claims'] = [c for c in data['claims'] if c.get('support_refs') and c.get('proposition_ids')
                      and _subject_claim_valid(c, subject_props, subject_assessments)
                      and all(ref.get('ref_type') != 'ARTIFACT' or ref.get('ref_id') in artifact_ids
                              for ref in c.get('support_refs',[]))
                      and all(owner_assessments.get(rid,{}).get('authority_status') == 'AUTHORITATIVE'
                              for pid in c.get('proposition_ids',[]) for rid in prop_owners.get(pid, [None]))
                      and c.get('epistemic') == 'SUPPORTED'
                      and not (set(c.get('proposition_ids') or []) & reset_propositions)
                      and set(c.get('proposition_ids') or []) <= ids]
    for prop in data['propositions']:
        prop['supporting_artifact_ids'] = [aid for aid in prop['supporting_artifact_ids'] if aid in artifact_ids]
        prop['supporting_claim_ids'] = [c['id'] for c in data['claims'] if prop['id'] in c.get('proposition_ids',[])]
        unproved = any(owner_assessments.get(rid, {}).get('authority_status') != 'AUTHORITATIVE'
                       for rid in prop_owners[prop['id']])
        unbacked = prop['kind'] in {k.value for k in COMMUNICABLE_KINDS} and not prop['supporting_claim_ids']
        unsupported = not (prop['supporting_claim_ids'] or prop['supporting_artifact_ids'] or prop['evidence_finding_ids'])
        if prop['coverage'] == 'SATISFIED' and (unbacked or unproved or unsupported):
            prop['coverage'] = 'PARTIAL'
            prop['gaps'].append('integrity recovery: satisfied obligation lacked approved authority/support')
    data['goal_status'] = 'WAITING_FOR_USER' if data.get('clarification') else 'LIMITED'
    data['limitations'].append('governed integrity recovery: result is limited; no authority was promoted')
    rebuilt = AnswerProjection.model_validate(data)
    # If an inconsistent status alone triggered recovery, do not promote to COMPLETE.
    if goal_status(rebuilt.propositions) == GoalStatus.COMPLETE and not rebuilt.clarification:
        for prop in rebuilt.propositions:
            if prop.mandatory:
                prop.coverage = CoverageState.PARTIAL
                prop.gaps.append('integrity recovery: upstream completion requires fresh evaluation')
    rebuilt.requirement_assessments = reaggregate_assessments(rebuilt)
    validate_governed_projection(rebuilt,expected=expected)
    return rebuilt.frozen_snapshot()
