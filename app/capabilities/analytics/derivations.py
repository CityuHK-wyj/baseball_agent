"""Finite semantic proofs for derived analytical Requirements.

Proof prose is an audit label, never evidence. Validators reconstruct both meanings
from payloads. Unknown representations fail closed; no model or SQL is consulted.
"""
from __future__ import annotations

import re
from typing import Any


def _text(req):
    bound = req.get('bound_source_clause')
    if bound:
        return str(bound)
    value = req.get('value')
    if isinstance(value, dict):
        return str(value.get('objective') or value.get('text') or value.get('mention') or '')
    return str(value or '')


def _profile(req, *, derived=False):
    # Lazy import: conformance calls authority, and these consumers have no authority
    # side effects. Only emitted obligations (not UNDERSTOOD labels) are proof.
    from app.capabilities.analytics.conformance import constraint_coverage, role_from_text
    from app.capabilities.analytics.measures import resolve_texts, MEASURES
    from app.capabilities.analytics.catalog import CATALOG
    from app.capabilities.analytics.predicates import canonical_predicate, conjunction_leaves
    kind, value = req.get('kind'), req.get('value')
    if kind == 'SEASON':
        if isinstance(value, bool) or not isinstance(value, int): raise ValueError('season')
        return {'season': value}
    if kind == 'TIME':
        if not isinstance(value, dict) or set(value) - {'start','end'}: raise ValueError('time')
        from datetime import date
        if set(value) != {'start','end'} or date.fromisoformat(str(value['start'])) > date.fromisoformat(str(value['end'])):
            raise ValueError('exact temporal window')
        return {'date_range': value}
    if kind == 'GAME_POPULATION':
        codes=sorted(CATALOG.resolve_population(str(value)))
        if not codes: raise ValueError('population')
        return {'game_types': codes}
    if kind == 'MEASURE':
        if not isinstance(value, dict): raise ValueError('measure')
        allowed = {'key','label','derivable','matched','note','requested_text','definition','operation_graph'}
        if set(value) - allowed or value.get('key') not in MEASURES: raise ValueError('measure payload')
        spec = MEASURES[value['key']]
        # Display/alias metadata participates in source accounting downstream. It
        # therefore cannot carry arbitrary extra semantic text on a derived value.
        if derived:
            names={s.casefold() for s in (spec.key,spec.label,*spec.aliases)}
            if any(str(value[k]).casefold() not in names for k in ('label','requested_text') if value.get(k)):
                raise ValueError('unproved metric surface')
        if 'definition' in value and value['definition'] != spec.definition: raise ValueError('definition')
        if 'operation_graph' in value:
            from app.capabilities.analytics.ir import AnalyticalQuery, validate_query
            from app.capabilities.analytics.measures import declare_measures
            graph=value['operation_graph']
            if not isinstance(graph,dict) or set(graph) - {'aggregates','derived'}: raise ValueError('operation graph payload')
            query=validate_query(AnalyticalQuery(relation='pitch',**graph))
            if not any(m.verified and m.key == spec.key for m in declare_measures(query)):
                raise ValueError('operation graph does not implement governed metric')
        return {'measures': [spec.key]}
    if kind == 'ENTITY':
        if not isinstance(value, dict): raise ValueError('entity')
        allowed = {'mention','canonical_name','canonical_id','canonical_ids','namespace','role',
                   'identity_verified','type_hint','status','carried'}
        if set(value) - allowed: raise ValueError('entity payload')
        result = {'entity_mention': str(value.get('mention') or value.get('canonical_name') or '').casefold()}
        ids = value.get('canonical_ids') or ([value['canonical_id']] if value.get('canonical_id') is not None else [])
        if ids:
            if any(isinstance(i,bool) or not str(i).isdigit() or int(i)<=0 for i in ids): raise ValueError('identity')
            result['entity_ids'] = sorted(int(i) for i in ids)
        if value.get('namespace'): result['namespace'] = value['namespace']
        if value.get('role'): result['analytical_role'] = value['role']
        if value.get('canonical_name'): result['entity_canonical_name'] = str(value['canonical_name']).casefold()
        if value.get('identity_verified'): result['identity_verified'] = True
        return result
    if kind not in {'CONSTRAINT','ANALYSIS'}: raise ValueError('kind')
    if kind == 'ANALYSIS' and isinstance(value, dict):
        if set(value) - {'objective','index','background_ok','source_text'}: raise ValueError('analysis payload')
        value = req.get('bound_source_clause') or value.get('objective')
    accounted = re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)|\d{4}-\d{2}-\d{2}',value) if isinstance(value,str) else []
    if isinstance(value,str):
        if re.search(r'regular[ -]season',value,re.I): accounted.extend(['regular season','regular-season'])
        if re.search(r'(?<!\d)(?:19|20)\d{2}\s+season',value,re.I): accounted.append('season')
        if any(m.key and m.key.startswith('avg_') for m in resolve_texts([value])): accounted.extend(['average','mean'])
    coverage = constraint_coverage(value, analysis=kind == 'ANALYSIS', accounted_terms=accounted)
    if coverage['state'] != 'COMPLETE': raise ValueError('unconsumed semantics cannot prove a transformation')
    result = dict(coverage['obligations'])
    if isinstance(value, str):
        years = sorted({int(y) for y in re.findall(r'(?<!\d)((?:19|20)\d{2})(?!\d)',value)})
        if len(years) == 1: result['season'] = years[0]
        dates = re.findall(r'(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)',value)
        if len(dates) == 2 and re.search(r'\b(?:from|between)\b|从|從',value,re.I):
            result['date_range'] = {'start':dates[0],'end':dates[1]}
        role = role_from_text(value)
        if role: result['analytical_role'] = role
        measures = sorted({m.key for m in resolve_texts([value]) if m.key})
        if measures: result['measures'] = measures
        if re.search(r'regular[ -]season|常规赛|常規賽|例行赛|例行賽',value,re.I): result['game_types'] = ['R']
    elif coverage['state'] != 'COMPLETE': raise ValueError('unknown structured semantics')
    if 'filters' in result: result['filters'] = tuple(canonical_predicate(c) for c in conjunction_leaves(result['filters']))
    return result


def default_valid(req):
    name, value = req.get('derivation_proof'), req.get('value')
    if req.get('kind') == 'GAME_POPULATION':
        return name == 'regular_season_competition_default' and value == 'REGULAR_SEASON'
    if req.get('kind') != 'CONSTRAINT': return False
    payloads = {
        'full_eligible_row_population': {'filters': []},
        'regular_season_competition_default': {'game_types': ['R']},
        'scalar_result_default': {'group_by': []},
        'no_qualification_default': {'qualification': None},
        'no_semantic_top_n_default': {'limit': None},
    }
    return name in payloads and value == payloads[name]


def transformation_valid(child, parents, *, identity_evidence=()):
    kind = child.get('derivation_kind')
    try:
        wanted = _profile(child, derived=True)
        if not wanted: return False
        # A grounded interpreter candidate may contribute a governed fragment without
        # covering its whole provenance span. Admission checks authority and conflicting
        # known meaning here; the source review independently preserves any unaccounted
        # residue (including unsupported or extra governed dimensions).
        if kind == 'governed_semantic_candidate':
            from app.semantic.analytical_candidate import assess_candidate
            value = child.get('value') if isinstance(child.get('value'), dict) else {}
            return any(assess_candidate(_text(parent), value, parents).admitted for parent in parents)
        available = {}
        for parent in parents:
            for key,value in _profile(parent).items():
                if key in available and available[key] != value: return False
                available[key] = value
        if kind == 'canonical_entity_identity':
            if child.get('kind') != 'ENTITY' or any(p.get('kind') != 'ENTITY' for p in parents): return False
            ids = wanted.get('entity_ids')
            if not ids or wanted.get('entity_mention') != available.get('entity_mention'): return False
            if ids != available.get('entity_ids') or not available.get('identity_verified'):
                # Evidence must come from runtime Binder, not the child proof/payload.
                if not any(e.get('namespace','MLBAM') == 'MLBAM' and sorted(int(i) for i in e.get('ids',[])) == ids
                           and available.get('entity_mention') in [str(n).casefold() for n in [e.get('mention',''),*e.get('names',[])]]
                           for e in identity_evidence): return False
            available = {**available,'entity_ids':ids,'namespace':'MLBAM','identity_verified':True}
            if 'entity_canonical_name' in wanted and wanted['entity_canonical_name'] != available.get('entity_canonical_name'):
                if not any(sorted(int(i) for i in e.get('ids',[])) == ids
                           and wanted['entity_canonical_name'] in [str(n).casefold() for n in e.get('names',[])]
                           for e in identity_evidence): return False
                available['entity_canonical_name']=wanted['entity_canonical_name']
        elif kind == 'resolved_temporal_scope':
            if set(wanted) != {'date_range'}: return False
            if 'date_range' not in available and 'season' in available:
                y = available['season']; available['date_range'] = {'start':f'{y}-01-01','end':f'{y}-12-31'}
        elif kind == 'governed_measure_definition':
            if set(wanted) != {'measures'}: return False
        elif kind == 'governed_population_codes':
            if set(wanted) != {'game_types'}: return False
        elif kind == 'schema_field_binding':
            if not set(wanted) <= {'filters','group_by','analytical_role'}: return False
        elif kind not in {'interpreter_requirement_binding','user_requirement_partition','synonym_normalization'}:
            return False
        # Only a represented sub-obligation may be partitioned. Lists require subset
        # membership (AND predicates), never arbitrary implication or proof prose.
        for key,value in wanted.items():
            if key not in available: return False
            if key in {'filters','group_by','measures'}:
                if any(v not in available[key] for v in value): return False
            elif value != available[key]: return False
        return True
    except (ValueError,TypeError,KeyError,AttributeError):
        return False
