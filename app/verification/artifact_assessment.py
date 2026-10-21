"""Judge-local data-quality/admissibility inspection, separate from Requirement coverage.

Native records have narrow predicate/identity/temporal/fragment semantics. Derived
computations require typed Requirement-local operation contracts and recursively
admitted exact cells. A routing Need, source label, program, syntactic dependency or
caller date cannot widen either contract. No acquisition or LLM calls occur here.
"""
from datetime import datetime, timezone
from hashlib import sha256
import json

from app.contracts.models import (
    ArtifactAssessment, ArtifactPropositionSupport, AuthorityNeed, TemporalMode,
)

STATSAPI_PEOPLE = 'https://statsapi.mlb.com/api/v1/people/'


def _identities(goal, artifacts):
    identities = {}
    for req in goal.requirements:
        if req.kind.value != 'ENTITY':
            continue
        value = req.value if isinstance(req.value, dict) else {}
        names = {str(n).strip().casefold() for n in
                 (value.get('mention'), value.get('canonical_name')) if n}
        ids = set()
        ref = req.entity_ref
        if ref and ref.identity_verified and ref.namespace == 'MLBAM' and ref.canonical_id is not None:
            ids.add(str(ref.canonical_id))
        for artifact in artifacts:
            if artifact.kind != 'ENTITY_RESOLUTION' or artifact.status != 'ACCEPTED':
                continue
            for row in artifact.structured_data.get('resolved', []):
                if str(row.get('mention', '')).strip().casefold() in names and row.get('id'):
                    ids.add(str(row['id']))
                    names.update(str(row[k]).strip().casefold() for k in ('name', 'canonical_name') if row.get(k))
            for mention, row in (artifact.structured_data.get('identity') or {}).items():
                if str(mention).strip().casefold() in names and row.get('level') == 'CANONICAL' and row.get('id'):
                    ids.add(str(row['id']))
                    if row.get('name'): names.add(str(row['name']).strip().casefold())
        for name in names:
            identities.setdefault(name, set()).update(ids)
    return identities


def _native_record(artifact, fact, envelopes):
    """Re-read the bounded provider envelope, not the adapter's asserted fact labels."""
    entity_id = str(fact.get('entity_id', ''))
    if not entity_id.isdecimal() or fact.get('relation') != 'TEAM_MEMBERSHIP':
        raise ValueError('unknown native predicate or invalid subject ID')
    expected_url = f'{STATSAPI_PEOPLE}{entity_id}?hydrate=currentTeam'
    envelope = next((e for e in envelopes if isinstance(e, dict) and e.get('url') == expected_url), None)
    if envelope is None:
        raise ValueError('missing provider response envelope')
    raw = envelope.get('raw_body')
    if not isinstance(raw, str) or len(raw.encode('utf-8')) > 1_000_000:
        raise ValueError('missing or oversized provider body')
    digest = sha256(raw.encode('utf-8')).hexdigest()
    if envelope.get('content_hash') != digest or fact.get('response_hash') != digest:
        raise ValueError('provider response hash mismatch')
    observed = envelope.get('retrieved_at')
    try:
        timestamp = datetime.fromisoformat(observed.replace('Z', '+00:00'))
    except (ValueError, AttributeError, TypeError):
        raise ValueError('invalid provider observation time') from None
    if timestamp.tzinfo is None or timestamp > datetime.now(timezone.utc):
        raise ValueError('unproved provider observation time')
    observed_date = timestamp.astimezone(timezone.utc).date().isoformat()
    if fact.get('as_of') != observed_date or artifact.structured_data.get('as_of') != observed_date:
        raise ValueError('caller as-of is not the provider observation time')
    payload = json.loads(raw)
    people = payload.get('people')
    if (not isinstance(people, list) or len(people) != 1 or not isinstance(people[0], dict)
            or type(people[0].get('id')) is not int or str(people[0]['id']) != entity_id):
        raise ValueError('provider subject identity mismatch')
    person = people[0]
    team = person.get('currentTeam') or {}
    if (not isinstance(team, dict) or type(team.get('id')) is not int or team['id'] <= 0
            or not isinstance(team.get('name'), str) or not team['name'].strip()
            or not isinstance(person.get('fullName'), str) or not person['fullName'].strip()):
        raise ValueError('absence or malformed native fields do not establish membership')
    if (fact.get('value') != team['name'] or str(fact.get('team_id')) != str(team['id'])
            or fact.get('player_name') != person.get('fullName') or fact.get('source_url') != expected_url):
        raise ValueError('fact does not resolve to its exact provider record fragment')
    return entity_id, observed_date, observed, digest, team


def _temporal_match(prop, observed_date, goal, owner):
    temporal = prop.temporal_expectation
    # Current-state evidence cannot prove a season or user-bound historical interval.
    if prop.time_scope.get('season'):
        return False
    window = prop.time_scope.get('date_range')
    if window:
        resolved = owner.resolved_scope
        # A semantic CONFIRMED_STATE request's *inferred* acquisition lookback is
        # freshness policy, not a request for membership throughout that interval.
        # Rebind against the owner's exact scope; artifact/caller dates cannot opt in.
        inferred_freshness = (owner.evidence_intent is not None
            and owner.evidence_intent.mode == 'CONFIRMED_STATE'
            and resolved is not None and resolved.kind == 'RELATIVE' and not resolved.confirmed
            and window == {'start':resolved.start,'end':resolved.end}
            and temporal is not None and temporal.start == resolved.start and temporal.end == resolved.end)
        if not inferred_freshness:
            return False
        return observed_date == str(resolved.anchor_date or goal.anchor_date)[:10]
    if temporal is not None:
        if temporal.mode in {TemporalMode.HISTORICAL, TemporalMode.DATE_RANGE}:
            return False
        if temporal.mode == TemporalMode.AS_OF:
            return temporal.as_of == observed_date
    anchor = (temporal.anchor_date if temporal else None) or goal.anchor_date
    return observed_date == (str(anchor)[:10] if anchor else datetime.now(timezone.utc).date().isoformat())


def assess_native_records(artifact, goal, propositions, artifacts):
    reasons, support = [], []
    data = artifact.structured_data or {}
    facts, envelopes = data.get('facts'), data.get('provider_responses')
    if data.get('relation') != 'TEAM_MEMBERSHIP' or not isinstance(facts, list) or not 0 < len(facts) <= 5:
        return ArtifactAssessment(artifact_id=artifact.id, source_kind='STRUCTURED_PROVIDER',
                                  status='REJECTED', reasons=['invalid provider relation/record collection'])
    if not isinstance(envelopes, list) or not 0 < len(envelopes) <= 5:
        return ArtifactAssessment(artifact_id=artifact.id, source_kind='STRUCTURED_PROVIDER',
                                  status='REJECTED', reasons=['no bounded inspectable provider envelopes'])
    identities = _identities(goal, artifacts)
    owners = {r.id: r for r in goal.requirements}
    for index, fact in enumerate(facts):
        if not isinstance(fact, dict):
            reasons.append('invalid native record')
            continue
        try:
            subject, observed_date, observed, digest, team = _native_record(artifact, fact, envelopes)
        except (ValueError, TypeError, KeyError, AttributeError):
            reasons.append('native record failed identity/time/hash/fragment inspection')
            continue
        for prop in propositions:
            relation = prop.relation_semantics
            owner = owners.get(prop.requirement_id)
            if (owner is None or owner.relation_semantics is None
                    or owner.relation_semantics.predicate != 'TEAM_MEMBERSHIP'
                    or relation is None or relation.predicate != 'TEAM_MEMBERSHIP'
                    or prop.kind.value not in {'FACT', 'GROUNDED'}
                    or prop.information_class in {'INJURY', 'TRANSACTION'}):
                continue
            if ((owner.evidence_intent is not None and owner.evidence_intent.mode != 'CONFIRMED_STATE')
                    or (prop.evidence_intent is not None and prop.evidence_intent.mode != 'CONFIRMED_STATE')
                    or (prop.authority_expectation is not None and prop.authority_expectation.need in
                        {AuthorityNeed.REPORTED_ACCOUNT, AuthorityNeed.FIRSTHAND_ACCOUNT})):
                continue
            if identities.get(prop.subject.strip().casefold(), set()) != {subject}:
                continue
            if not _temporal_match(prop, observed_date, goal, owner):
                continue
            # A named target is not an empty answer slot. Do not assert a requested
            # target from a different team record; negative inference needs its own proof.
            if relation.relation_target and relation.relation_target not in {str(team['id']), team['name']}:
                continue
            support.append(ArtifactPropositionSupport(proposition_id=prop.id, record_index=index,
                locator='/people/0/currentTeam', predicate='TEAM_MEMBERSHIP', subject_id=subject,
                observed_at=observed, response_hash=digest))
    if not support:
        reasons.append('no proposition-local predicate, subject and temporal match')
    return ArtifactAssessment(artifact_id=artifact.id, source_kind='STRUCTURED_PROVIDER',
        status='ELIGIBLE' if support else 'QUALIFIED',
        eligible_proposition_ids=sorted({s.proposition_id for s in support}), record_support=support,
        reasons=list(dict.fromkeys(reasons)))


def assess_artifacts(artifacts, goal, propositions, analytical_support, findings, inspections):
    """One inspectable result per collected Artifact; no coverage or Claim authority.

    Derived authority is exclusively delegated to OperationAdmission; no alternate
    AST, scope, measure or legacy lane can bypass that Requirement-local decision.
    """
    from app.verification.governed_operations import OperationAdmission
    admission = OperationAdmission(artifacts, goal, {p.requirement_id for p in propositions},
                                   _identities(goal, artifacts))
    result = {}
    for artifact in artifacts:
        if artifact.status not in {'ACCEPTED', 'PARTIAL'}:
            assessment = ArtifactAssessment(artifact_id=artifact.id, source_kind=artifact.kind,
                                             status='REJECTED', reasons=['unaccepted collected artifact'])
        elif artifact.kind == 'DYNAMIC_FACT':
            assessment = assess_native_records(artifact, goal, propositions, artifacts)
        elif artifact.kind == 'DATABASE_RESULT':
            eligible = [p.id for p in propositions if p.requirement_id in analytical_support.get(artifact.id, set())
                        and p.kind.value != 'COMPARISON' and p.reporting_index is None]
            assessment = ArtifactAssessment(artifact_id=artifact.id, source_kind='DATABASE',
                status='ELIGIBLE' if eligible else 'REJECTED', eligible_proposition_ids=eligible,
                reasons=[] if eligible else ['receipt does not conform to current Requirement scope'])
        elif artifact.kind == 'DERIVED_MEASURE':
            derived = admission.assess(artifact.id)
            if derived.status == 'ELIGIBLE':
                eligible = [p.id for p in propositions
                            if p.kind.value not in {'FACT', 'EVENT', 'EXPLANATION'}
                            and p.reporting_index is None
                            and not (p.evidence_intent and p.evidence_intent.mode == 'PREDICTION')
                            and p.requirement_id in derived.derived_requirement_ids]
                derived = derived.model_copy(update={'eligible_proposition_ids': eligible,
                    'status': 'ELIGIBLE' if eligible else 'QUALIFIED',
                    'reasons': derived.reasons + ([] if eligible else ['no matching derived obligation'])})
            assessment = derived
        elif artifact.kind == 'WEB_EVIDENCE':
            owned = {f.get('id') for f in artifact.structured_data.get('evidence_findings', []) if isinstance(f, dict)}
            eligible = sorted({f.proposition_id for f in findings if f.id in owned and f.status.value == 'SUPPORTED'})
            assessment = ArtifactAssessment(artifact_id=artifact.id, source_kind='WEB_BODY',
                status='ELIGIBLE' if eligible else 'QUALIFIED', eligible_proposition_ids=eligible,
                inspection_ids=[i.id for i in inspections if i.finding_id in owned])
        else:
            assessment = ArtifactAssessment(artifact_id=artifact.id, source_kind=artifact.kind,
                status='LEGACY_UNNORMALIZED', reasons=['compatibility evidence path; full normalization pending'])
        result[artifact.id] = assessment
    return result
