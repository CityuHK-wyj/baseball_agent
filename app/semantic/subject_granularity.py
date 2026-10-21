"""Conserve subject output units, without interpreting open language or approving facts.

Input ENTITY sets are not permission to pool their statistics. The interpreter's
finite output-granularity proposal is kept separately from input population. A
legacy/unknown aggregate reading cannot authorize a numerical aggregation.
"""
from __future__ import annotations


def subject_requirements(requirement, requirements):
    """Current, Requirement-owned subjects of a non-scalar statistical obligation.

    Exact names are reference binding, not a task-verb/wording classifier. Source
    names also conserve omitted references; a candidate cannot silently drop B.
    Typed reporting/operation contracts already have their own exact output units.
    """
    req = requirement if isinstance(requirement, dict) else requirement.model_dump(mode='json')
    reqs = [r if isinstance(r, dict) else r.model_dump(mode='json') for r in requirements]
    if (req.get('kind') not in {'ANALYSIS', 'MEASURE'} or req.get('completion') == 'EXPLANATION'
            or req.get('reporting_contract') or req.get('operation_contract')):
        return []
    analyses = [r for r in reqs if r.get('kind') == 'ANALYSIS' and r.get('completion') != 'EXPLANATION'
                and not r.get('reporting_contract') and not r.get('operation_contract')]
    if not analyses:
        return []
    role = (req.get('value') or {}).get('analytical_role') if isinstance(req.get('value'), dict) else None
    # MEASURE inherits the statistical context, not permission to change that role.
    if not role:
        role = next(((r.get('value') or {}).get('analytical_role') for r in analyses
                     if isinstance(r.get('value'), dict) and (r.get('value') or {}).get('analytical_role')), None)
    semantics = req.get('relation_semantics') or {}
    if (role not in {'BATTER', 'PITCHER'}
            and semantics.get('output_granularity') not in {'PER_SUBJECT', 'POOLED'}
            and semantics.get('relation_kind') not in {'AGGREGATION', 'VALUE'}):
        return []
    refs = {str(s).strip().casefold() for s in semantics.get('subject_refs') or []}
    value = req.get('value') if isinstance(req.get('value'), dict) else {}
    source = str(req.get('bound_source_clause') or value.get('source_text') or '').casefold()
    entities = [r for r in reqs if r.get('kind') == 'ENTITY' and r.get('required', True)]
    all_names = {str((e.get('value') or {}).get(k) or '').strip().casefold()
                 for e in entities if isinstance(e.get('value'), dict) for k in ('mention', 'canonical_name')}
    unbound_refs = bool(refs - all_names)
    selected = []
    for entity in entities:
        value = entity.get('value') if isinstance(entity.get('value'), dict) else {}
        names = {str(value.get(k) or '').strip().casefold() for k in ('mention', 'canonical_name')}
        names.discard('')
        if not refs or unbound_refs or names & refs or any(n in source for n in names):
            selected.append(entity)
    return selected


def subject_grouping(requirements, role):
    """Closed-world output dimension, never inferred from Planner hints or row count."""
    if role not in {'BATTER', 'PITCHER'}:
        return []
    entities = [r for r in requirements if
                (r.get('kind') if isinstance(r, dict) else r.kind.value) == 'ENTITY'
                and (r.get('required', True) if isinstance(r, dict) else r.required)]
    return [dict(BATTER='batter_id', PITCHER='pitcher_id')[role]] if len(entities) > 1 and any(
        subject_requirements(r, requirements) for r in requirements) else []


def refine_subject_propositions(goal, propositions, partitions=()):
    """Refine existing slots (including persisted legacy ones), conserving owners.

    Comparisons keep their independent relation obligation; individual observations
    do not prove that relation. Unknown legacy output additionally retains an
    unresolved slot: a coarse relation label cannot choose sum vs mean vs individuals.
    """
    from app.contracts.models import PropositionKind, new_id
    if sum(r.kind.value == 'ENTITY' and r.required for r in goal.requirements) < 2:
        # No cross-subject pooling is possible under a uniquely bound identity.
        # Preserve existing single-subject split/grouping and evidence paths.
        return propositions
    reqs = {r.id: r for r in goal.requirements}
    result = []
    refined_owners = {p.requirement_id for p in propositions if p.subject_granularity is not None}
    replacements = {}
    for prop in propositions:
        start = len(result)
        req = reqs.get(prop.requirement_id)
        context = goal.requirements
        if req and req.kind.value == 'MEASURE':
            owners = [part for part in partitions if any(r.id == req.id for r in part.requirements)]
            if owners:
                owned = {r.id for part in owners for r in part.requirements}
                context = [r for r in goal.requirements if r.id in owned]
        subjects = subject_requirements(req, context) if req else []
        if not subjects or prop.requirement_id in refined_owners:
            result.append(prop)
            continue
        semantics = req.relation_semantics
        grain = semantics.output_granularity if semantics else 'UNRESOLVED'
        if req.kind.value == 'MEASURE' and grain == 'UNRESOLVED':
            # A global Measure inherits the same individual output units, not an
            # additional ambiguity, when every owning statistical analysis agrees.
            analyses = [r for r in context if r.kind.value == 'ANALYSIS'
                        and r.completion != 'EXPLANATION' and not r.reporting_contract and not r.operation_contract]
            if analyses and all(r.relation_semantics and r.relation_semantics.output_granularity == 'PER_SUBJECT'
                                for r in analyses):
                grain = 'PER_SUBJECT'
        if prop.kind == PropositionKind.COMPARISON:
            result.append(prop)  # never substitute a value for a comparative predicate
        elif grain == 'POOLED':
            result.append(prop.model_copy(update={'subject_granularity': 'POOLED'}))
            continue  # no finite operation contract: no numeric pooling authority
        if grain == 'UNRESOLVED':
            result.append(prop.model_copy(update={'id': new_id('prop'),
                'kind': PropositionKind.GROUNDED, 'subject_granularity': 'UNRESOLVED'}))
        for entity in subjects:
            value = entity.get('value') or {}
            result.append(prop.model_copy(deep=True, update={'id': new_id('prop'),
                'kind': PropositionKind.GROUNDED if prop.kind == PropositionKind.COMPARISON else prop.kind,
                'comparison': None, 'subject': value.get('mention') or value.get('canonical_name') or '',
                'subject_requirement_ids': [entity['id']], 'subject_granularity': 'PER_SUBJECT'}))
        replacements[prop.id] = [p.id for p in result[start:]]
        if prop.kind != PropositionKind.COMPARISON and replacements[prop.id]:
            result[start].id = prop.id
            replacements[prop.id][0] = prop.id
    for prop in result:
        prop.depends_on = list(dict.fromkeys(pid for dep in prop.depends_on
            for pid in replacements.get(dep, [dep])))
    return result
