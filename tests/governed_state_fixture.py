"""Explicitly approved synthetic Judge snapshots for presentation/lifecycle tests.

Not an evidence-admission helper: never use this to test a production Judge. These
fixtures declare the authority their mocked Judge was already intended to supply.
"""
from app.contracts.models import Reference, RequirementAssessment, ArtifactAssessment
from app.verification.governed_state import reaggregate_assessments, validate_governed_projection


def approved_snapshot(projection):
    for claim in projection.claims:
        if not claim.support_refs:
            claim.support_refs = [Reference(ref_type='ARTIFACT',ref_id='synthetic-approved-evidence')]
    for prop in projection.propositions:
        prop.supporting_claim_ids = [c.id for c in projection.claims if prop.id in c.proposition_ids]
    known = {a.artifact_id for a in projection.artifact_assessments}
    aids = set(projection.supporting_artifact_ids) | {aid for p in projection.propositions for aid in p.supporting_artifact_ids}
    aids.update(ref.ref_id for c in projection.claims for ref in c.support_refs if ref.ref_type.value == 'ARTIFACT')
    projection.artifact_assessments.extend(ArtifactAssessment(artifact_id=aid,source_kind='SYNTHETIC_FIXTURE',status='ELIGIBLE')
                                         for aid in sorted(aids-known))
    existing = {a.requirement_id:a.model_dump(mode='json') for a in projection.requirement_assessments}
    projection.requirement_assessments = [RequirementAssessment.model_validate({
        **existing.get(rid,{'requirement_id':rid,'status':'UNSATISFIED'}),
        'authority_status':'AUTHORITATIVE'}) for rid in sorted({p.requirement_id for p in projection.propositions})]
    projection.requirement_assessments = reaggregate_assessments(projection)
    validate_governed_projection(projection)
    return projection.frozen_snapshot()
