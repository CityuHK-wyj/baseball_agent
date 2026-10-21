#!/usr/bin/env python3
"""M2.11 read-only diagnostic runner. Never imported by production.

Run snapshot/local/diagnostic-live/holdout separately. Captures public research and
analytical results explicitly for this audit; never serializes configuration secrets.
"""
from __future__ import annotations

import argparse
import copy
import dataclasses
import datetime as dt
import hashlib
import json
import math
import random
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs/audit/m211"


def dump(name, value):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


def encode(value):
    if hasattr(value, "model_dump"):
        # The current PostgreSQL schema envelope contains psycopg2 type objects.
        # Preserve their printable type, without changing the production artifact.
        return encode(value.model_dump(mode="python"))
    if dataclasses.is_dataclass(value):
        return {f.name: encode(getattr(value, f.name)) for f in dataclasses.fields(value)
                if f.name != "trace"}
    if isinstance(value, dict):
        return {str(k): encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(v) for v in value]
    return value


def equal(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
    return a == b


class Recorder:
    def __init__(self):
        self.rows = []

    def check(self, name, actual, expected, category, **detail):
        ok = equal(actual, expected)
        self.rows.append(dict(name=name, category=category, ok=ok,
                              actual=encode(actual), expected=encode(expected), **encode(detail)))
        return ok

    def save(self, name):
        groups = {}
        for row in self.rows:
            g = groups.setdefault(row["category"], {"passed": 0, "failed": 0})
            g["passed" if row["ok"] else "failed"] += 1
        dump(name, {"summary": groups, "checks": self.rows})
        print(json.dumps(groups), flush=True)


def snapshot():
    """Old run observations, exact persisted Goal, and labelled controlled replays."""
    from app.contracts.models import Goal, RetrievedDocument, EvidenceSpan, AnswerProposition
    from app.capabilities.web_research import WebResearchTool
    from app.config import WebConfig
    from app.semantic.decomposition import factual_core, _strip_subject, _strip_carriers
    from app.verification.lineage import text_hash
    from app.verification.evidence import verify_span
    from app.verification.propositions import build_propositions
    conn = sqlite3.connect(f"file:{ROOT / '.runtime/conversations.db'}?mode=ro", uri=True)
    sessions = {s: json.loads(p) for s, p in conn.execute(
        "SELECT session_id,payload FROM conversations WHERE session_id LIKE 'm210_postfreeze_%'")}
    conn.close()
    cases = []
    for group in ("legacy", "rotating"):
        report = json.loads((ROOT / f"docs/benchmarks/m210_postfreeze_{group}_live.json").read_text())
        adj = json.loads((ROOT / f"docs/benchmarks/m210_postfreeze_{group}_finding_adjudications.json").read_text())
        for task_id, runs in report["runs"].items():
            session = f"m210_postfreeze_{group}_{task_id}_0"
            goals = sessions[session]["goals"]
            goal_data = goals[0] if isinstance(goals, list) else next(iter(goals.values()))
            goal = Goal.model_validate(goal_data)
            run = runs[0]
            false = []
            for f in run["evidence_findings"]:
                if f["status"] != "SUPPORTED" or adj.get(f["id"]) is not False:
                    continue
                prop = next(p for p in goal.propositions if f["id"] in p.evidence_finding_ids)
                req = next(r for r in goal.requirements if r.id == prop.requirement_id)
                text = f["span_text"]
                prov = next(d for d in run["document_provenance"] if d["id"] == f["document_id"])
                # Body/offsets were not retained in the benchmark. This creates a NEW
                # one-span document to test the current admission mechanism, not old replay.
                document = RetrievedDocument(id=f["document_id"], url=prov["url"],
                    retrieved_at=prov["retrieved_at"], retrieval_status="OK",
                    content_hash=text_hash(text), body_text=text)
                span = EvidenceSpan(id=f["span_id"], document_id=document.id,
                    text=text, start=0, end=len(text))
                tool = WebResearchTool(WebConfig(tavily_api_key=""), None)
                finding = {"document_id": document.id, "evidence_spans": [encode(span)]}
                admission = tool._verify([prop], [finding], [encode(document)])
                intact = copy.deepcopy(prop)
                intact.required_information = goal.raw_query
                intact.statement = goal.raw_query
                expanded = verify_span(intact, span, document, llm=None)
                false.append({"historical_finding": f, "provenance": prov,
                    "requirement": encode(req), "proposition": encode(prop),
                    "carrier_transform": {
                        "factual_core": factual_core(req.statement),
                        "after_subject_removal": _strip_subject(factual_core(req.statement),
                            [r.entity_ref.canonical_name or r.entity_ref.surface for r in goal.requirements if r.entity_ref]),
                        "after_carrier_removal": _strip_carriers(_strip_subject(factual_core(req.statement),
                            [r.entity_ref.canonical_name or r.entity_ref.surface for r in goal.requirements if r.entity_ref]))},
                    "controlled_current_admission": encode(admission),
                    "full_question_lexical_control": encode(expanded),
                    "duplicate_text_hash": text_hash(text)})
            reconstructed = build_propositions(goal, llm=None)
            # Historical diagnostics contain metadata only. Do not pretend redacted fields
            # contain the historical full query plan, rank queue, verifier or claim text.
            diag_records = []
            for p in (ROOT / '.runtime/diagnostics').rglob('*.json'):
                if session not in str(p):
                    continue
                raw = json.loads(p.read_text())
                diag_records.extend(raw if isinstance(raw, list) else raw.get("records", []))
            cases.append({"set": group, "task_id": task_id, "session": session,
                "goal": goal_data, "historical_run": run, "false_findings": false,
                "deterministic_proposition_reconstruction": encode(reconstructed),
                "historical_metadata": diag_records,
                "capture_limits": ["semantic objectives reconstructable from persisted requirements",
                    "historical full semantic message, search return queue, scores, native payloads and claims not retained",
                    "one-span controlled admission replay does not establish old offsets or old LLM decision"]})
    dump("historical-traces.json", cases)
    print("historical tasks", len(cases), "false findings", sum(len(c["false_findings"]) for c in cases), flush=True)


def local():
    from app.config import get_config
    from app.capabilities.analytics.executor import AnalyticalExecutor, DuckDBExecutor
    from app.capabilities.analytics.catalog import CATALOG, SchemaCatalog, TableSpec, SOURCE_POSTGRES, SOURCE_PARQUET
    from app.capabilities.analytics.compiler import SQLCompiler
    from app.capabilities.analytics.ir import AnalyticalQuery, validate_query
    from app.capabilities.analytics.guard import guard_sql
    from app.capabilities.analytics.tool import LocalAnalyticsTool
    from app.capabilities.analytics.measures import declare_measures, condition_tag, resolve_texts
    from app.contracts.models import (ToolRequest, Artifact, Export, PlannerNeed, Scope,
        SemanticInterpretation, EntityMention, AnalysisRequest, Constraint, ConversationState)
    from app.semantic.goals import RequirementBuilder
    from app.runtime.context import ExecutionContext
    from app.runtime.binder import RuntimeBinder
    from app.capabilities.registry import CapabilityRegistry
    from app.capabilities.entity_resolution import EntityResolutionTool
    from app.knowledge.directory import PlayerDirectory
    from app.verification.judge import Judge
    from app.semantic.temporal import resolve_temporal_scope
    from app.verification.enumeration import check_compatibility

    cfg = get_config()
    executor = AnalyticalExecutor(cfg.postgres, cfg.parquet.archive_path)
    compiler = SQLCompiler(parquet_archive=cfg.parquet.archive_path)
    rec = Recorder()
    inventory = {}
    for source, table in [(SOURCE_POSTGRES, 'public.statcast_pitches'),
                          (SOURCE_PARQUET, f"read_parquet('{cfg.parquet.archive_path}/mlb_statcast_*.parquet')")]:
        r = executor.run(f"SELECT MIN(game_date) AS first, MAX(game_date) AS last, COUNT(*) AS n FROM {table}", source)
        inventory[source] = encode(r)
        rec.check(f"source_{source}", not bool(r.error), True, "availability")
    if inventory[SOURCE_POSTGRES]['error']:
        dump('inventory.json', inventory)
        rec.save('local-checks.json')
        return
    inventory['postgres_columns'] = executor.run("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema='public' AND table_name='statcast_pitches' ORDER BY ordinal_position", SOURCE_POSTGRES).rows
    inventory['postgres_permissions'] = executor.run("SELECT current_user AS role, current_setting('transaction_read_only') AS readonly, has_table_privilege(current_user,'public.statcast_pitches','INSERT') AS can_insert, has_table_privilege(current_user,'public.statcast_pitches','UPDATE') AS can_update, has_table_privilege(current_user,'public.statcast_pitches','DELETE') AS can_delete", SOURCE_POSTGRES).rows
    inventory['partitions'] = sorted(p.name for p in Path(cfg.parquet.archive_path).glob('*.parquet'))
    inventory['parquet_schema'] = executor.run(f"DESCRIBE SELECT * FROM read_parquet('{cfg.parquet.archive_path}/mlb_statcast_2023.parquet')", SOURCE_PARQUET).rows
    rec.check('postgres_readonly', inventory['postgres_permissions'][0]['readonly'], 'on', 'database_safety')
    rec.check('role_write_privileges', [inventory['postgres_permissions'][0][k] for k in ['can_insert','can_update','can_delete']], [False]*3, 'database_safety')
    dump('inventory.json', inventory)

    ids = executor.run("SELECT batter_id, COUNT(events) AS n FROM public.statcast_pitches WHERE game_date BETWEEN '2024-06-01' AND '2024-06-30' AND events IS NOT NULL GROUP BY batter_id HAVING COUNT(events)>50 ORDER BY batter_id LIMIT 30", SOURCE_POSTGRES).rows
    samples = [int(r['batter_id']) for r in ids]
    dump('generated-manifest.json', {'seeds': list(range(21100,21124)), 'entity_ids': samples,
        'anchor': '2026-09-14', 'reference': 'independent hand-written SQL; rates compared numerically',
        'not_holdout': True})
    def run_query(q, source):
        validated = validate_query(AnalyticalQuery.model_validate(q))
        compiled = compiler.compile(validated, source)
        if not guard_sql(compiled.sql).ok:
            raise ValueError('guard rejected compiled SELECT')
        return executor.run(compiled.sql, source), compiled

    # Genuine data, independently constructed expected SELECTs, seeded ids/time/category.
    generated = []
    for seed in range(21100,21124):
        rng = random.Random(seed)
        player = rng.choice(samples)
        year = rng.choice([2023,2024,2025])
        source = SOURCE_PARQUET if year < 2024 else SOURCE_POSTGRES
        month = rng.choice([5,6,7,8]); handed = rng.choice(['L','R']); event = rng.choice(['single','home_run','strikeout'])
        threshold = rng.choice([85,90,95,100])
        q = {'season': year, 'population':'REGULAR_SEASON',
            'entity_filters':[{'role':'BATTER','ids':[player]}],
            'date_range':{'start':f'{year}-{month:02}-01','end':f'{year}-{month:02}-28'},
            'filters':[{'op':'COMPARE','field':'p_throws','comparator':'=','value':handed}],
            'aggregates':[{'name':'n','function':'COUNT','field':'*'},
                {'name':'pa','function':'COUNT_NON_NULL','field':'events'},
                {'name':'mean_ev','function':'AVG','field':'launch_speed'},
                {'name':'selected_event','function':'COUNT_IF','condition':{'op':'COMPARE','field':'events','comparator':'=','value':event}},
                {'name':'hard','function':'COUNT_IF','condition':{'op':'COMPARE','field':'launch_speed','comparator':'>=','value':threshold}},
                {'name':'bbe','function':'COUNT_NON_NULL','field':'launch_speed'}],
            'derived':[{'name':'hard_pct','op':'PCT','left':{'kind':'aggregate','name':'hard'},'right':{'kind':'aggregate','name':'bbe'}}]}
        result, compiled = run_query(q, source)
        table = 'public.statcast_pitches' if source == SOURCE_POSTGRES else f"read_parquet('{cfg.parquet.archive_path}/mlb_statcast_{year}.parquet')"
        col = 'batter_id' if source == SOURCE_POSTGRES else 'batter'
        ref_sql = f"SELECT COUNT(*) AS n, COUNT(events) AS pa, AVG(launch_speed) AS mean_ev, SUM(CASE WHEN events='{event}' THEN 1 ELSE 0 END) AS selected_event, SUM(CASE WHEN launch_speed>={threshold} THEN 1 ELSE 0 END) AS hard, COUNT(launch_speed) AS bbe, 100.0*SUM(CASE WHEN launch_speed>={threshold} THEN 1 ELSE 0 END)/NULLIF(COUNT(launch_speed),0) AS hard_pct FROM {table} WHERE {col}={player} AND game_type='R' AND game_date BETWEEN '{year}-{month:02}-01' AND '{year}-{month:02}-28' AND p_throws='{handed}'"
        ref = executor.run(ref_sql, source)
        rec.check(f'generated_{seed}', result.rows, ref.rows, f'result_correctness_{source}', ir=q, compiled_sql=compiled.sql, reference_sql=ref_sql, execution_error=result.error)
        generated.append({'seed':seed, 'ir':q, 'compiled':encode(compiled), 'result':encode(result), 'reference':encode(ref), 'reference_sql':ref_sql})
    dump('generated-results.json', generated)

    # Ranking, qualification, prefixes, reversed order, threshold and window properties.
    for source, year in [(SOURCE_POSTGRES,2024),(SOURCE_PARQUET,2023)]:
        base = {'season':year, 'population':'REGULAR_SEASON','group_by':['batter_id'],
            'aggregates':[{'name':'pa','function':'COUNT_NON_NULL','field':'events'}],
            'sort':[{'name':'pa','direction':'DESC'},{'name':'batter_id','direction':'ASC'}], 'limit':10,
            'qualification':{'basis':'PA','minimum':300}}
        r10, _ = run_query(base, source)
        q=copy.deepcopy(base);q['limit']=5;r5,_=run_query(q,source)
        rec.check(f'prefix_{source}',r5.rows,r10.rows[:5],'metamorphic')
        q=copy.deepcopy(base);q['limit']=500;q['sort']=[];a,_=run_query(q,source)
        q['qualification']['minimum']=400;b,_=run_query(q,source)
        rec.check(f'threshold_subset_{source}',{x['batter_id'] for x in b.rows}.issubset({x['batter_id'] for x in a.rows}),True,'metamorphic')
        q=copy.deepcopy(base);q['qualification']['minimum']=10**8;empty,_=run_query(q,source)
        rec.check(f'no_qualifiers_{source}',empty.rows,[],'qualification')
        q=copy.deepcopy(base);q['qualification']['minimum']=0;unqualified,_=run_query(q,source)
        rec.check(f'unqualified_size_{source}',len(unqualified.rows)>=len(r10.rows),True,'qualification')
        table='public.statcast_pitches' if source==SOURCE_POSTGRES else f"read_parquet('{cfg.parquet.archive_path}/mlb_statcast_{year}.parquet')"
        col='batter_id' if source==SOURCE_POSTGRES else 'batter'
        ref=executor.run(f"SELECT {col} AS batter_id,COUNT(events) AS pa FROM {table} WHERE game_type='R' AND game_date BETWEEN '{year}-01-01' AND '{year}-12-31' GROUP BY {col} HAVING COUNT(events)>=300 ORDER BY pa DESC,{col} ASC LIMIT 10",source)
        rec.check(f'ranking_reference_{source}',r10.rows,ref.rows,'qualification')
        top=r10.rows[0]; n=top['pa']; player=top['batter_id']
        q={'season':year,'population':'REGULAR_SEASON','entity_filters':[{'role':'BATTER','ids':[player]}], 'aggregates':[{'name':'pa','function':'COUNT_NON_NULL','field':'events'}], 'qualification':{'basis':'PA','minimum':n}}
        yes,_=run_query(q,source);q['qualification']['minimum']=n+1;no,_=run_query(q,source)
        rec.check(f'meets_exact_{source}',len(yes.rows),1,'qualification')
        rec.check(f'misses_one_{source}',no.rows,[],'qualification')
        q={'season':year,'population':'REGULAR_SEASON','entity_filters':[{'role':'BATTER','ids':[player]}], 'aggregates':[{'name':'n','function':'COUNT','field':'*'}],'date_range':{'start':f'{year}-06-01','end':f'{year}-06-14'}}
        short,_=run_query(q,source);q['date_range']['end']=f'{year}-06-30';wide,_=run_query(q,source)
        rec.check(f'window_monotonic_{source}',wide.rows[0]['n']>=short.rows[0]['n'],True,'metamorphic')
        # Sorting a bounded same-population identity set both ways is exact even with ties.
        q={'season':year,'entity_filters':[{'role':'BATTER','ids':samples[:6]}], 'group_by':['batter_id'],'aggregates':[{'name':'n','function':'COUNT','field':'*'}],'sort':[{'name':'batter_id','direction':'ASC'}]}
        asc,_=run_query(q,source);q['sort'][0]['direction']='DESC';desc,_=run_query(q,source)
        rec.check(f'sort_inverse_{source}',desc.rows,list(reversed(asc.rows)),'metamorphic')

    # IR/schema/enum adversarial samples; categoricals fail before SQL execution.
    base={'season':2024,'aggregates':[{'name':'n','function':'COUNT','field':'*'}]}
    def validation(raw):
        try:
            q=validate_query(AnalyticalQuery.model_validate(raw));compiled=compiler.compile(q,SOURCE_POSTGRES)
            return {'valid':True,'guard':guard_sql(compiled.sql).ok,'sql':compiled.sql}
        except Exception as e:
            return {'valid':False,'code':getattr(e,'code',type(e).__name__)}
    for field in ['rbi','stolen_bases','batter','pitcher','team','league','player_role','not_a_column']:
        q=copy.deepcopy(base);q['group_by']=[field]
        rec.check(f'unknown_field_{field}',validation(q)['valid'],False,'schema')
    for field, good, bad in [('stand','L','l'),('p_throws','R','LEFT'),('pitch_type','FF','fastball'),('events','home_run','HOME_RUN'),('game_type','R','regular')]:
        for value, expected in [(good,True),(bad,False),('X_UNKNOWN',False),("R'; SELECT 1; --",False)]:
            q=copy.deepcopy(base);q['filters']=[{'op':'COMPARE','field':field,'comparator':'=','value':value}]
            rec.check(f'enum_{field}_{value}',validation(q)['valid'],expected,'categorical_enumeration')
    for field,value,expected in [('balls',3,True),('strikes',2,True),('balls',99,False),('strikes',-1,False),('stand',42,False),('events',42,False),('release_speed',True,False)]:
        q=copy.deepcopy(base);q['filters']=[{'op':'COMPARE','field':field,'comparator':'=','value':value}]
        rec.check(f'type_domain_{field}_{value}',validation(q)['valid'],expected,'ir_domain_safety')
    q=copy.deepcopy(base);q['filters']=[{'op':'AND','conditions':[{'op':'COMPARE','field':'balls','comparator':'=','value':3},{'op':'COMPARE','field':'strikes','comparator':'=','value':2}]}]
    rec.check('compound_count',validation(q)['valid'],True,'categorical_enumeration')
    for statement in ['DROP TABLE pitch','DELETE FROM pitch','SELECT 1; SELECT 2','SELECT 1 -- comment','SELECT pg_read_file(\'/etc/passwd\')','WITH x AS (DELETE FROM pitch RETURNING *) SELECT * FROM x','SELECT 1']:
        rec.check('guard_'+statement,guard_sql(statement).ok,statement=='SELECT 1','database_safety')
    for year,expected in [(2014,None),(2015,SOURCE_PARQUET),(2023,SOURCE_PARQUET),(2024,SOURCE_POSTGRES),(2026,SOURCE_POSTGRES),(2027,None)]:
        rec.check(f'route_{year}',CATALOG.source_for_window('pitch',season=year)[0],expected,'routing')

    class ScriptedIR:
        configured=True
        def __init__(self,q):self.q=q
        def complete_json(self,**kwargs):return SimpleNamespace(data=copy.deepcopy(self.q))
    tool=LocalAnalyticsTool(executor,None,parquet_archive=cfg.parquet.archive_path)
    # Honest drift detection is tested through the real tool + Judge, with explicitly
    # injected IR candidates. No LLM outcome is imputed from these controls.
    player=samples[0]; other=samples[1]
    def tool_case(name, ir, intent=None, inputs=None):
        tool.llm=ScriptedIR(ir)
        req=ToolRequest(need_id='analytic',capability='local_analytics',objective='count home runs', inputs={'intent':{'objective':'count home runs','season':2024,'population':'REGULAR_SEASON',**(intent or {})}, 'entity_sets':[{'ids':[player],'names':['audit player'],'role':'BATTER'}],**(inputs or {})})
        result=tool.execute(req)
        return result, req
    valid={'season':2024,'population':'REGULAR_SEASON','entity_filters':[{'role':'BATTER','ids':[player]}],'aggregates':[{'name':'home_runs','function':'COUNT_IF','condition':{'op':'COMPARE','field':'events','comparator':'=','value':'home_run'}}]}
    semantic=SemanticInterpretation(raw_query='How many home runs did audit player hit in the 2024 regular season?',entities=[EntityMention(mention='audit player',type_hint='player')],analysis_requests=[AnalysisRequest(text='count home runs',objective='count home runs')],constraints=[Constraint(kind='season',value=2024),Constraint(kind='measure',value='home runs')],population_hints=['REGULAR_SEASON'])
    goal=RequirementBuilder().build(semantic,anchor_date=dt.date(2026,9,14))
    entity=Artifact(kind='ENTITY_RESOLUTION',need_id='entity',structured_data={'resolved':[{'mention':'audit player','id':player,'name':'audit player'}], 'identity':{'audit player':{'level':'CANONICAL','id':player,'name':'audit player'}}},actual_scope=Scope(entities=[str(player)],entity_names=['audit player']),status='ACCEPTED')
    controls=[]
    for name,change in [('valid',{}),('wrong_player',{'entity_filters':[{'role':'BATTER','ids':[other]}]}),('no_entity_filter',{'entity_filters':[]}),('wrong_population',{'game_types':['S']}),('wrong_season',{'season':2025})]:
        q=copy.deepcopy(valid);q.update(change);result,request=tool_case(name,q)
        projection=Judge().evaluate(copy.deepcopy(goal),[entity]+([result.artifact] if result.artifact else []),[])
        complete=projection.goal_status.value=='COMPLETE'
        rec.check(f'drift_{name}',complete,name=='valid','analytical_scope_lineage', ir=q, result=encode(result), projection=encode(projection))
        controls.append({'name':name,'request':encode(request),'ir_injected':q,'result':encode(result),'goal':encode(goal),'projection':encode(projection)})
    # Requested qualification is not applied if omitted by IR; Judge must not attest it.
    constrained=copy.deepcopy(goal)
    from app.contracts.models import UserRequirement, RequirementKind
    constrained.requirements.append(UserRequirement(kind=RequirementKind.CONSTRAINT,statement='minimum PA 100000000',value={'basis':'PA','minimum':100000000}))
    result,request=tool_case('qualification_omitted',valid,intent={'qualification':{'basis':'PA','minimum':100000000}})
    projection=Judge().evaluate(constrained,[entity,result.artifact],[])
    rec.check('qualification_omission',projection.goal_status.value=='COMPLETE',False,'qualification', result=encode(result),projection=encode(projection))
    controls.append({'name':'qualification_omitted','request':encode(request),'result':encode(result),'projection':encode(projection)})
    # Availability and cross-boundary source gaps.
    q=copy.deepcopy(valid);q['season']=None;q['date_range']={'start':'2023-06-01','end':'2024-06-30'}
    result,request=tool_case('cross_boundary',q,intent={'season':None,'date_range':q['date_range']})
    rec.check('cross_boundary_disclosure',bool(result.artifact and ('partial coverage' in result.outcome.message.lower() or result.artifact.status!='ACCEPTED')),True,'routing', result=encode(result))
    controls.append({'name':'cross_boundary','request':encode(request),'result':encode(result)})
    q=copy.deepcopy(valid);q['season']=2026;q['date_range']={'start':'2026-09-15','end':'2026-09-30'}
    result,request=tool_case('data_after_snapshot',q,intent={'season':2026,'date_range':q['date_range']})
    controls.append({'name':'data_after_snapshot','request':encode(request),'result':encode(result)})
    # Legitimate zero from an existing population and empty population need separate states.
    q=copy.deepcopy(valid);q['aggregates'].insert(0,{'name':'n','function':'COUNT','field':'*'})
    q['filters']=[{'op':'COMPARE','field':'release_speed','comparator':'>','value':200}]
    result,request=tool_case('empty_population',q)
    rec.check('empty_population',result.outcome.code,'VALID_EMPTY','zero_rows')
    controls.append({'name':'empty_population','request':encode(request),'result':encode(result)})
    q=copy.deepcopy(valid);q['aggregates'].insert(0,{'name':'n','function':'COUNT','field':'*'})
    q['filters']=[{'op':'COMPARE','field':'events','comparator':'=','value':'walk'}]
    result,request=tool_case('true_zero_hr',q)
    rec.check('valid_zero_hr',result.outcome.code,'OK','zero_rows')
    # invalid field and unavailable table return typed errors, not zero-valued evidence.
    q=copy.deepcopy(valid);q['aggregates']=[{'name':'x','function':'SUM','field':'rbi'}]
    result,request=tool_case('invalid_interpretation',q)
    rec.check('invalid_is_not_zero',result.outcome.code,'UNKNOWN_FIELD','zero_rows')
    dump('controlled-tool-traces.json',controls)

    # Verify Measure governance with operator/denominator/units transformations.
    for event,key in [('home_run','home_runs'),('single','singles'),('strikeout','strikeouts')]:
        q=copy.deepcopy(valid);q['aggregates'][0]['condition']['value']=event
        declared=declare_measures(validate_query(AnalyticalQuery.model_validate(q)))
        rec.check('attested_'+event,declared[0].key,key,'measure')
    q=copy.deepcopy(valid);q['aggregates'][0]={'name':'home_runs','function':'COUNT','field':'*'}
    rec.check('named_hr_is_pitches',declare_measures(validate_query(AnalyticalQuery.model_validate(q)))[0].key,'pitches','measure')
    for minimum in [95,96,100]:
        tag=condition_tag(AnalyticalQuery.model_validate({'aggregates':[{'name':'hard','function':'COUNT_IF','condition':{'op':'COMPARE','field':'launch_speed','comparator':'>=','value':minimum}}]}).aggregates[0].condition)
        rec.check(f'hard_hit_definition_{minimum}',tag,'hard_hit' if minimum==95 else None,'measure')
    for word in ['ERA','RBI','stolen bases']:
        resolutions=list(resolve_texts([word]));rec.check('unsupported_'+word,bool(resolutions) and not resolutions[0].derivable,True,'measure')

    # Real directory ambiguity plus generated duplicates and aliases through Binder.
    directory=PlayerDirectory(executor)
    class Store:
        def match_alias(self,n):return None
        def record_candidate(self,*a,**k):pass
    er=EntityResolutionTool(Store(),directory,None)
    registry=CapabilityRegistry();registry.register(tool);binder=RuntimeBinder(registry)
    name_rows=executor.run(f"SELECT player_id,player_name FROM public.player_dictionary WHERE player_id IN ({','.join(map(str,samples[:6]))}) ORDER BY player_id",SOURCE_POSTGRES).rows
    for mention,hint,want_empty in [('Smith','player',True),('NoSuchPlayerM211xyz','player',True),('Dodgers','team',True)]+[(r['player_name'],'player',False) for r in name_rows]:
        request=ToolRequest(need_id='identity',capability='entity_resolution',objective='resolve',inputs={'mentions':[{'mention':mention,'type_hint':hint}]})
        result=er.execute(request);artifact=result.artifact or (result.extra_artifacts[0] if result.extra_artifacts else None)
        need=PlannerNeed(id='consumer',objective='compute',capability='local_analytics',depends_on=['identity'],input_requirements=['PLAYER_ID_SET'])
        bound=binder.bind(need,[],[artifact] if artifact else [])
        rec.check('binding_'+mention,not bound.ok,want_empty,'binder',entity_outcome=encode(result.outcome),binding=encode(bound),lookup=directory.search_by_name(mention,limit=6))
    for seed in range(21200,21212):
        rng=random.Random(seed);first=f'Player{rng.randrange(1000)}';last=f'Surname{rng.randrange(1000)}'
        candidates=[{'id':seed*2,'name':f'{last}, {first}'},{'id':seed*2+1,'name':f'{last}, {first}'}]
        rec.check(f'duplicate_name_{seed}',er._choose(f'{first} {last}',candidates),None,'binder')
        rec.check(f'invalid_alias_{seed}',er._choose('invalidname',[candidates[0]]),None,'entity_alias_safety')
    rec.check('alias_order',er._choose('First Last',[{'id':1,'name':'Last, First'},{'id':2,'name':'Last, Other'}])['id'],1,'binder')
    for ids_value,namespace,expected in [([], 'MLBAM',False),([player],'OTHER',False),([player],'MLBAM',True),(['not-an-id'],'MLBAM',False)]:
        art=Artifact(kind='ENTITY_RESOLUTION',need_id='identity',exports=[Export(id='audit_export',type='PLAYER_ID_SET',payload={'ids':ids_value},contract={'namespace':namespace})],status='ACCEPTED')
        bound=binder.bind(PlannerNeed(id='consumer',objective='compute',capability='local_analytics',depends_on=['identity']),[],[art])
        rec.check(f'binder_payload_{ids_value}_{namespace}',bound.ok,expected,'binder')

    anchor=dt.date(2026,9,14)
    for text,expected in [('last 15 days',('2026-08-31','2026-09-14')),('last 30 days',('2026-08-16','2026-09-14')),('current season',('2026-03-01','2026-11-30')),('previous season',('2025-03-01','2025-11-30')),('before 2024-06-15',(None,'2024-06-14')),('after 2024-06-15',('2024-06-16',None)),('2024-06-01 to 2024-06-30',('2024-06-01','2024-06-30'))]:
        scope=resolve_temporal_scope(text,anchor_date=anchor)
        rec.check('temporal_'+text,(scope.start,scope.end),expected,'temporal',scope=encode(scope))
    a=resolve_temporal_scope('last 15 days',anchor_date=anchor)
    b=resolve_temporal_scope('last 15 days',anchor_date=anchor)
    rec.check('stable_scope',encode(a),encode(b),'temporal')
    # Explicit constraints with season and dates must not erase a narrower time window.
    scope=resolve_temporal_scope('last 15 days',anchor_date=anchor,constraints=[Constraint(kind='season',value=2026)])
    rec.check('season_plus_relative',scope.is_window(),True,'temporal',scope=encode(scope))
    rec.check('last15_inclusive_day_count',(dt.date.fromisoformat(a.end)-dt.date.fromisoformat(a.start)).days+1,15,'temporal')

    # Equivalent engine results on a genuine PostgreSQL sample copied read-only into
    # a temporary Parquet. This is engine parity, not production year overlap.
    import duckdb
    mirror_dir=Path('/tmp/m211-parity');mirror_dir.mkdir(exist_ok=True)
    mirror=mirror_dir/'mlb_statcast_mirror.parquet'
    fields=list(CATALOG.table('pitch').key_map[SOURCE_POSTGRES])
    sample=executor.run('SELECT '+','.join('EXTRACT(YEAR FROM game_date) AS season' if f=='season' else '"'+f+'"' for f in fields)+" FROM public.statcast_pitches WHERE game_date BETWEEN '2024-06-01' AND '2024-06-03'",SOURCE_POSTGRES)
    if sample.error:
        rec.check('parity_sample',sample.error,'','postgres_parquet_parity')
        rec.save('local-checks.json')
        return
    con=duckdb.connect()
    definitions=','.join('"'+f+'" '+{'int':'BIGINT','float':'DOUBLE','string':'VARCHAR','date':'DATE'}[CATALOG.field('pitch',f).type] for f in fields)
    con.execute('CREATE TABLE sample ('+definitions+')')
    con.executemany('INSERT INTO sample VALUES ('+','.join('?' for _ in fields)+')',[[row.get(f) for f in fields] for row in sample.rows])
    con.execute(f"COPY sample TO '{mirror}' (FORMAT PARQUET)");con.close()
    spec=CATALOG.table('pitch');mirror_spec=dataclasses.replace(spec,physical={**spec.physical,SOURCE_PARQUET:f"read_parquet('{mirror}')"},key_map={**spec.key_map,SOURCE_PARQUET:spec.key_map[SOURCE_POSTGRES]})
    mirror_catalog=SchemaCatalog({'pitch':mirror_spec})
    mirror_compiler=SQLCompiler(mirror_catalog,parquet_archive=str(mirror_dir))
    engine= DuckDBExecutor(str(mirror_dir))
    parity=[]
    for seed in range(21300,21316):
        rng=random.Random(seed)
        field=rng.choice(['stand','p_throws','pitch_type','balls','events'])
        q={'date_range':{'start':'2024-06-01','end':'2024-06-03'},'group_by':[field],
           'aggregates':[{'name':'n','function':'COUNT','field':'*'},{'name':'nonnull','function':'COUNT_NON_NULL','field':'launch_speed'},{'name':'v','function':'AVG','field':'release_speed'}]}
        validated=validate_query(AnalyticalQuery.model_validate(q));p=compiler.compile(validated,SOURCE_POSTGRES);d=mirror_compiler.compile(validated,SOURCE_PARQUET)
        rp=executor.run(p.sql,SOURCE_POSTGRES);rd=engine.run(d.sql)
        ordered=lambda rows:sorted(rows,key=lambda row:str(row[field]))
        rec.check(f'parity_{seed}',ordered(rp.rows),ordered(rd.rows),'postgres_parquet_parity',pg_sql=p.sql,duck_sql=d.sql)
        parity.append({'seed':seed,'ir':q,'postgres':encode(rp),'parquet':encode(rd)})
    dump('parity.json',{'sample_rows':sample.row_count,'sample_window':['2024-06-01','2024-06-03'],'mirror_hash':hashlib.sha256(mirror.read_bytes()).hexdigest(),'cases':parity})
    rec.save('local-checks.json')


def live_tasks(tasks, filename):
    """Real Engine, audit wrappers preserve each boundary without modifying production."""
    from app.config import get_config
    from app.runtime.engine import Engine
    from app.runtime.context import SystemClock
    from app.response.answer_contract import build_answer_package
    from app.capabilities.web_research import _required_hint_tokens
    class FixedClock(SystemClock):
        def today(self):return dt.date(2026,9,14)
        def now(self):return dt.datetime(2026,9,14,12,tzinfo=dt.timezone.utc)
    def run(task):
        started=time.monotonic()
        cfg=get_config(); llm=dataclasses.replace(cfg.llm,runtime_model='deepseek-chat',semantic_model='deepseek-chat',planner_model='deepseek-chat',analytics_model='deepseek-chat',response_model='deepseek-chat',timeout_seconds=25)
        cfg=dataclasses.replace(cfg,llm=llm,runtime_dir=Path('/tmp/m211-'+filename+'-'+task['id']))
        engine=Engine(cfg);engine.clock=FixedClock()
        capture={'task':task,'planner_calls':[],'tool_requests':[],'tool_results':[],'judge_calls':[],'web_searches':[],'web_selection':[],'ir_candidates':[],'llm_errors':[]}
        # Methods are wrapped per instance; every wrapper delegates unchanged inputs.
        def wrap(obj,name,records,format_args):
            original=getattr(obj,name)
            def invoke(*args,**kw):
                item=format_args(args,kw)
                try:r=original(*args,**kw)
                except Exception as e:
                    item['exception']=type(e).__name__+': '+str(e);records.append(encode(item));raise
                item['result']=encode(r);records.append(encode(item));return r
            setattr(obj,name,invoke)
        wrap(engine.planner,'plan',capture['planner_calls'],lambda a,k:{'input':k})
        wrap(engine.judge,'evaluate',capture['judge_calls'],lambda a,k:{'goal':a[0] if a else k.get('goal'),'artifact_ids':[x.id for x in (a[1] if len(a)>1 else k.get('artifacts',[]))]})
        for capability in engine.registry.names():
            tool=engine.registry.get(capability)
            wrap(tool,'execute',capture['tool_requests'],lambda a,k:{'request':a[0] if a else k.get('request')})
        web=engine.registry.get('web_research')
        wrap(web,'_search',capture['web_searches'],lambda a,k:{'args':a,'kwargs':k})
        original_retrieve=web._retrieve
        def retrieve(hits,objective,queries,subject_tokens,budget,propositions=None,latency=None):
            selected={'ranked_hits':encode(hits),'objective':objective,'queries':queries,'subject_tokens':sorted(subject_tokens),'budget':budget,'propositions':encode(propositions)}
            r=original_retrieve(hits,objective,queries,subject_tokens,budget,propositions,latency)
            selected['retrieval_result']=encode(r);capture['web_selection'].append(selected);return r
        web._retrieve=retrieve
        analytics=engine.registry.get('local_analytics')
        wrap(analytics,'_build_ir',capture['ir_candidates'],lambda a,k:{'args':a,'kwargs':k})
        try:
            result=engine.ask('m211_'+task['id'],task['query'],trace_level='off')
            capture['turn']=encode(result)
            capture['answer_package']=encode(build_answer_package(result.goal,result.projection,result.artifacts,language=result.semantic.language))
            capture['usage']=engine.llm.usage_records
        except Exception as e:
            capture['exception']=type(e).__name__+': '+str(e)
        capture['duration_s']=round(time.monotonic()-started,3)
        dump(filename+'-'+task['id']+'.json',capture)
        print(task['id'],capture.get('turn',{}).get('projection',{}).get('goal_status',capture.get('exception')),flush=True)
        return capture
    with ThreadPoolExecutor(max_workers=3) as pool:
        results=list(pool.map(run,tasks))
    dump(filename+'.json',{'tasks':tasks,'runs':[{'id':r['task']['id'],'duration_s':r['duration_s'],'goal_status':r.get('turn',{}).get('projection',{}).get('goal_status'),'exception':r.get('exception')} for r in results]})


def diagnosis_live():
    legacy=json.loads((ROOT/'tools/data/dogfood_tasks_m2.json').read_text())
    rotating=json.loads((ROOT/'tools/data/m210_rotating_unseen_tasks.json').read_text())
    live_tasks(legacy+rotating,'diagnostic-live')


def analytics_live():
    live_tasks([
        {'id':'split_regression','query':'2025年大谷翔平面对左投和右投的打击率分别是多少？'},
        {'id':'qualified_ranking_regression','query':'2025年常规赛至少有300个打席的打者中，本垒打最多的是谁？'},
        {'id':'cross_source_regression','query':'大谷翔平2023赛季和2024赛季各打出了多少支本垒打？相差多少？'},
    ],'analytics-live')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['snapshot','local','diagnostic-live','analytics-live','holdout']);args=parser.parse_args()
    if args.mode=='snapshot':snapshot()
    elif args.mode=='local':local()
    elif args.mode=='diagnostic-live':diagnosis_live()
    elif args.mode=='analytics-live':analytics_live()
    else:
        frozen=OUT/'DIAGNOSIS_FROZEN.md'
        if not frozen.exists():raise SystemExit('freeze diagnosis before creating or running holdout')
        live_tasks(json.loads((OUT/'holdout-tasks.json').read_text()),'holdout')
