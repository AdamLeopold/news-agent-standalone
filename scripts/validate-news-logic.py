#!/usr/bin/env python3
"""Versioned, isolated real-model comparisons. Never writes production SQLite."""
import argparse
import asyncio
import ast
import copy
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
import re
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'reports/repair_2026-10-20'
parser=argparse.ArgumentParser()
parser.add_argument('--engine',choices=['original','candidate'],default='candidate')
fixture_options=parser.add_mutually_exclusive_group()
fixture_options.add_argument('--prepare',action='store_true')
fixture_options.add_argument('--case-file',type=Path,help='Read an explicit separate case JSON; never prepares or overwrites the original fixture')
parser.add_argument('--case',action='append')
parser.add_argument('--detail',action='store_true')
parser.add_argument('--run-id',default='final')
parser.add_argument('--reasoning',choices=['low','medium','high'])
parser.add_argument('--semantic-high-scope',choices=['operator_notice','all'],default=None,
                    help='Candidate semantic high-retry scope; omit to use the production default')
parser.add_argument('--allow-paused-live-queue',action='store_true',
                    help='Skip queued live jobs only while fresh local current-version health proves inference.enabled=false')
args=parser.parse_args() if __name__=='__main__' else parser.parse_args([])
source=OUT/'original_source' if args.engine=='original' and not args.prepare else ROOT
os.environ.setdefault('TIKTOKEN_CACHE_DIR',str(ROOT/'data/tokenizer'))
sys.path.insert(0,str(source))
from ttf_news.analysis import NewsAnalyst
from ttf_news.calendar import session_at
from ttf_news.prompts import PROMPT_VERSION

def dump(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2))


LOCAL_HEALTH_URL='http://127.0.0.1:8765/api/health'


def current_product_prompt_version():
    """The frozen original engine cannot define the live product version."""
    tree=ast.parse((ROOT/'ttf_news/prompts.py').read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node,ast.Assign) and any(isinstance(target,ast.Name) and target.id=='PROMPT_VERSION' for target in node.targets):
            value=ast.literal_eval(node.value)
            if isinstance(value,str):return value
    raise ValueError('Current product prompt version is unavailable')


def load_case_file(path=None,engine='candidate',requested=None):
    selected=Path(path) if path is not None else OUT/'comparison_cases.json'
    raw=selected.read_bytes();cases=json.loads(raw)
    if not isinstance(cases,list) or not cases:raise ValueError('Case file must contain a nonempty JSON array')
    identifiers=[]
    for row in cases:
        if not isinstance(row,dict) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,160}',str(row.get('id',''))):raise ValueError('Case id is missing or unsafe')
        if not isinstance(row.get(engine+'_context'),dict) or not isinstance(row.get('synthetic'),bool) or 'expected_news' not in row:raise ValueError('Case lacks the selected engine context, synthetic flag or explicit news expectation')
        identifiers.append(row['id'])
    if len(set(identifiers))!=len(identifiers):raise ValueError('Duplicate case ids in explicit fixture')
    unknown=set(requested or [])-set(identifiers)
    if unknown:raise ValueError('Requested cases do not exist in selected fixture: '+','.join(sorted(unknown)))
    try:label=str(selected.resolve().relative_to(ROOT.resolve()))
    except ValueError:label=selected.name
    return cases,{'file':label,'sha256':hashlib.sha256(raw).hexdigest(),'explicit_case_file':path is not None}


def explicit_future_well_action_check(result: dict, case: dict) -> dict:
    """A repair-specific real-source control, with no direction requirement."""
    required=case.get('required_explicit_future_well_action') is True
    if not required:return {'required':False,'passed':True,'evidence_paths':[]}
    paths=[]
    candidates=[]
    for section in ('news_impact','analysis'):
        value=result.get(section) or {}
        if isinstance(value.get('summary'),str):candidates.append((section+'.summary',value['summary']))
        if section=='analysis':
            for index,text in enumerate(value.get('evidence') or []):
                if isinstance(text,str):candidates.append((f'analysis.evidence[{index}]',text))
    future=r'(?:将(?:会|要)?|即将|计划|拟|预计|下一步|will(?:\s+now)?|is\s+(?:planned|scheduled|set)\s+to)'
    action=r'(?:封堵|封井|弃置|弃井|plug(?:ged|ging)?|abandon(?:ed|ment)?)'
    for path,text in candidates:
        aliases=re.findall(r'\[((?:N|E|P)\d+)\]',text)
        if aliases and 'N0' not in aliases:continue
        for clause in re.split(r'[。.!?！？;；\n]',text):
            for match in re.finditer(future+r'.{0,45}?'+action,clause,re.I):
                before=clause[max(0,match.start()-12):match.start()]
                if re.search(r'若|如果|假如|假设|倘若|\bif\b|(?:不会|不再|并非|否认|无需|没有|未曾|不|未|not)\s*$',before,re.I):continue
                if re.search(r'不|未|否认|无需|\bnot\b',match.group(),re.I):continue
                paths.append(path);break
            if path in paths:break
    return {'required':True,'passed':bool(paths),'evidence_paths':sorted(set(paths)),
            'scope':'Current N0 factual summaries/evidence must explicitly retain the source future plugging/abandonment action; bare action nouns or hypothetical scenarios do not satisfy this control.'}



def anchor_cases_from_frozen(frozen,legal_case,source_fixture_sha256):
    """Four separate v2.1 controls; never edits the original fifteen rows."""
    real=copy.deepcopy(frozen['input_snapshot'])
    base={'synthetic':False,'requires_original_comparison':False,
          'source_analysis_id':'6601a466-eecc-48f0-82e5-68785d8ac5be',
          'source_fixture':'tests/fixtures/manual_stale_previous_settlement_6601.json',
          'source_fixture_sha256':source_fixture_sha256}
    cases=[{**base,'id':'anchor_real_manual_missing_prevday','expected_news':'flat','expected_close':None,
            'fixture_design':{'purpose':'Replay the complete real v2.0 input under v2.1; reference19 cannot anchor target21 relative to previous20.','synthetic_price':False},
            'candidate_context':copy.deepcopy(real),'original_context':copy.deepcopy(real)}]
    texts={
        'loss':('up','隔离合成：欧洲实际供气减少40mcm/d',
                '隔离合成控制，以下情景按成立分析，不是真实报道：本结算窗口内，运营方和独立出口计量确认输往欧洲的实际天然气流量突发减少40 mcm/d，预计持续七天。该中断在当前窗口首次公布并立即生效。没有给出新增供应、需求、库存或天气反向事实，也未提供公告前后可比价格反应。'),
        'restore':('down','隔离合成：欧洲实际供气提前恢复40mcm/d',
                   '隔离合成控制，以下情景按成立分析，不是真实报道：本结算窗口内，运营方和独立出口计量确认输往欧洲的实际天然气流量提前恢复并增加40 mcm/d，恢复持续有效，早于此前预计仍将中断七天的计划。该恢复在当前窗口首次公布并立即生效。没有给出新增需求或其他反向供需事实，也未提供公告前后可比价格反应。')}
    for name,(direction,title,text) in texts.items():
        context=copy.deepcopy(real);identifier='anchor_gap_supply_'+name+'_40'
        news={'id':'synthetic-'+identifier,'source':'synthetic_anchor_control','synthetic_fixture':True,
              'title':title,'summary':text,'published_at':real['as_of'],'first_seen_at':real['as_of'],
              'timestamp_precision':'exact','timestamp_source':'isolated_synthetic_fixture','url':'','version':1}
        # Build every current text field afresh. Old EPCM summaries, daily
        # aggregates and previous news must not leak into these causal controls.
        context.update(current_news=news,window_news=[copy.deepcopy(news)],daily_aggregate={},
                       previous_sessions=[],historical_events=[],analysis_kind='manual')
        context['coverage']={'observation_limits':{'weather':'not_measured','storage':'not_measured',
                                                 'actual_flow':'synthetic_confirmation_in_current_news'}}
        cases.append({**base,'id':identifier,'synthetic':True,'expected_news':direction,
                      'close_contract':'source_supported_conditional_forecast_or_insufficient_evidence',
                      'fixture_design':{'purpose':'Same missing previous-day anchor; pressure sign is strict, closing may be an evidenced conditional forecast or null. Never calculate a yesterday move from reference19.','synthetic_news':True,'synthetic_price':False},
                      'candidate_context':context,'original_context':copy.deepcopy(context)})
    context=copy.deepcopy(legal_case['candidate_context']);old_id=context['current_news'].get('id')
    new_id='synthetic-anchor-legal-prevday-weak-market-up'
    for news in [context['current_news'],*context.get('window_news',[])]:
        if news.get('id')==old_id:news.update(id=new_id,source='synthetic_anchor_control',synthetic_fixture=True)
    cases.append({'id':'anchor_legal_prevday_weak_market_up','synthetic':True,'requires_original_comparison':False,
                  'expected_news':'flat','expected_close':'up',
                  'fixture_design':{'base_case':'weak_news_observed_market_up','purpose':'Keep a valid same-contract previous-trading-day price and observed upward baseline available under the new anchor rule.','synthetic_news':True,'price_scope':'Frozen legal original control, not a new live quote'},
                  'candidate_context':context,'original_context':copy.deepcopy(context)})
    return cases


def validate_paused_live_health(payload,expected_version,now=None):
    if not isinstance(payload,dict) or payload.get('prompt_version')!=expected_version:raise RuntimeError('Paused-queue override requires the current local product version')
    if payload.get('status')!='ok':raise RuntimeError('Local product health is not ready')
    inference=payload.get('inference')
    if not isinstance(inference,dict) or inference.get('enabled') is not False:raise RuntimeError('Paused-queue override requires explicit inference.enabled=false')
    if not isinstance(payload.get('server_pid'),int) or isinstance(payload.get('server_pid'),bool) or payload['server_pid']<=0:raise RuntimeError('Local product process identity missing')
    try:server_time=datetime.fromisoformat(str(payload['server_time']).replace('Z','+00:00'))
    except (KeyError,TypeError,ValueError) as exc:raise RuntimeError('Local health current server time missing') from exc
    if server_time.tzinfo is None:raise RuntimeError('Local health server time has no timezone')
    now=now or datetime.now(timezone.utc)
    if not -5<=(now-server_time).total_seconds()<=30:raise RuntimeError('Local health server time is stale or future')
    return {'mode':'inference_paused_verified','health_url':LOCAL_HEALTH_URL,'prompt_version':expected_version,
            'server_pid':payload['server_pid'],'server_time':payload['server_time'],
            'checked_at':now.isoformat(),'inference_enabled':False}


async def verify_paused_local_health(client=None,expected_version=None,now=None):
    import httpx
    version=expected_version or current_product_prompt_version()
    if client is None:
        async with httpx.AsyncClient(timeout=10,trust_env=False,follow_redirects=False) as local:
            return await verify_paused_local_health(local,version,now)
    response=await client.get(LOCAL_HEALTH_URL,headers={'Cache-Control':'no-cache'})
    response.raise_for_status()
    return validate_paused_live_health(response.json(),version,now)


def pending_live_jobs():
    with sqlite3.connect(f'file:{ROOT}/data/ttf_news.sqlite3?mode=ro',uri=True) as db:
        return db.execute("select count(*) from jobs where status in ('queued','running')").fetchone()[0]


async def wait_for_live_queue(allow_paused=False):
    if allow_paused:return await verify_paused_local_health()
    for _ in range(60):
        pending=pending_live_jobs()
        if not pending:return {'mode':'live_priority','pending_jobs_at_start':0}
        print('WAIT_LIVE_QUEUE',pending,flush=True);await asyncio.sleep(5)
    raise RuntimeError('Live queue stayed busy; resume this comparison later')


def add_flat_baseline_controls(cases):
    """Append isolated quote controls without changing the frozen no-quote cases."""
    from ttf_news.context import market_baseline
    from ttf_news.calendar import parse_time,iso
    result=copy.deepcopy(cases)
    by_id={row['id']:row for row in cases}
    template=by_id['weak_news_observed_market_up']
    for original,expected in (('supply_loss_40','up'),('supply_restore_40','down')):
        identifier=original+'_with_flat_baseline'
        control=copy.deepcopy(by_id[original])
        control.update(id=identifier,synthetic=True,expected_news=expected,expected_close_sign=expected,
                       requires_original_comparison=False,
                       fixture_design={'base_news_case':original,'price_template_case':'weak_news_observed_market_up',
                                       'synthetic_price':True,'previous_and_latest_price':88.04,
                                       'purpose':'新增当日方向性供需事实配合冲击前持平参考；检验条件方向，不测量实际结算准确率'})
        for key in ('original_context','candidate_context'):
            context=control[key]
            price=copy.deepcopy(template[key]['price_reference'])
            as_of=parse_time(context['as_of'])
            price.update(previous_price=88.04,synthetic_fixture=True,
                         reference_source='isolated synthetic flat-baseline control',source_url='',
                         threshold_frozen_as_of=context['as_of'])
            price['intraday'].update(value=88.04,contract=price['contract'],
                                     observed_at=iso(as_of-timedelta(minutes=20)),
                                     available_at=iso(as_of-timedelta(seconds=1)),
                                     source='synthetic_fixture',source_url='',synthetic_fixture=True,stale=False)
            price['market_baseline']=market_baseline(price,context['session'],context['as_of'])
            price['market_baseline']['synthetic_fixture']=True
            if not price['market_baseline'].get('available') or price['market_baseline'].get('observed_delta')!=0 or price['market_baseline'].get('observed_direction')!='flat':
                raise ValueError('Synthetic flat-baseline control has inconsistent price identity/time')
            context['price_reference']=price
        if identifier in by_id:
            if by_id[identifier]!=control:
                raise ValueError('Existing versioned control differs; preserve it and version changes explicitly: '+identifier)
        else:
            result.append(control)
    return result


def append_json_rows(original,new_rows):
    """Retain every original row's JSON bytes, including manually enriched replays."""
    if not new_rows:return original
    text=original.decode('utf-8')
    existing=json.loads(text)
    if not isinstance(existing,list):raise ValueError('Comparison fixture must be a JSON array')
    end=len(text.rstrip())-1
    if text[end]!=']':raise ValueError('Comparison JSON closing array is missing')
    added=json.dumps(new_rows,ensure_ascii=False,indent=2)
    if not existing:return (added+text[end+1:]).encode('utf-8')
    return (text[:end].rstrip()+','+added[1:-1]+']'+text[end+1:]).encode('utf-8')


def save_prepared(cases,original_bytes):
    before=json.loads(original_bytes)
    existing_ids={row['id'] for row in before}
    additions=[row for row in cases if row['id'] not in existing_ids]
    if not additions:
        print('Prepared',len(cases),'isolated cases; existing fixture bytes unchanged')
        return
    archived=OUT/'comparison_cases_v2.json'
    if not archived.exists():archived.write_bytes(original_bytes)
    target=OUT/'comparison_cases.json'
    content=append_json_rows(original_bytes,additions)
    if json.loads(content)!=cases:raise ValueError('Appending controls would change existing fixture rows')
    temporary=target.with_suffix('.json.tmp');temporary.write_bytes(content);temporary.replace(target)
    print('Prepared',len(cases),'isolated cases;',len(before),'existing rows preserved byte-for-byte')


def enrich_real_replay(store,context):
    """Reproduce candidate revision evidence without re-reading current news facts."""
    from ttf_news.context import enrich_news_revision
    result=copy.deepcopy(context)
    as_of=result['as_of']
    result['current_news']=enrich_news_revision(store,result.get('current_news'),as_of)
    result['window_news']=[enrich_news_revision(store,row,as_of) for row in result.get('window_news',[])]
    return result

def prepare():
    target=OUT/'comparison_cases.json'
    if target.exists():
        original=target.read_bytes()
        # Existing real replays already freeze article versions, as-of prices
        # and observed revision evidence. --prepare must never reset them.
        save_prepared(add_flat_baseline_controls(json.loads(original)),original)
        return
    from ttf_news.context import market_baseline
    asof='2026-10-20T12:10:58.642680+00:00'
    seed=json.loads((ROOT/'reports/flat_audit_2026-10-20/synthetic_down_current_prompt.json').read_text())
    up=json.loads((ROOT/'reports/flat_audit_2026-10-20/synthetic_up_current_prompt.json').read_text())['input']['current_news']['facts']
    down=seed['input']['current_news']['facts']
    weak=json.loads((ROOT/'reports/flat_audit_2026-10-20/synthetic_weak_current_prompt.json').read_text())['input']['current_news']['facts']
    def context(text,price=False):
        n={'id':'diagnostic-current','title':'隔离假设情景','summary':text,'source':'diagnostic_fixture',
           'published_at':asof,'first_seen_at':asof,'timestamp_precision':'exact','url':'','version':1}
        ref=copy.deepcopy(seed['input']['price_reference']) if price else {'available':False,'verified':False,'missing_fields':['previous_contract_price','intraday_quote','sigma60']}
        if price:
            ref['intraday']['contract']=ref['contract']
            ref['intraday'].setdefault('available_at',asof)
            ref['market_baseline']=market_baseline(ref,session_at(asof),asof)
        return {'as_of':asof,'session':session_at(asof),'analysis_kind':'manual','current_news':n,
                'window_news':[copy.deepcopy(n)],'previous_sessions':[],'historical_events':[],
                'price_reference':ref,'coverage':{'observation_limits':{'weather':'not_measured','storage':'not_measured','actual_flow':'not_measured'}},'lexicon':{}}
    cases=[]
    def add(name,text,expected,price=False,close='omit'):
        ctx=context(text,price)
        row={'id':name,'synthetic':True,'original_context':copy.deepcopy(ctx),'candidate_context':ctx,'expected_news':expected}
        if close!='omit':row['expected_close']=close
        cases.append(row);return row
    add('supply_loss_40',up,'up')
    add('supply_restore_40',down,'down')
    add('weak_exploration_no_quote',weak,'flat',close=None)
    add('warm_heating_demand','隔离假设：欧洲官方天气预报将未来七天气温上修4摄氏度，预计采暖天然气需求下降15%。本窗口首次发布，未给供应中断或其他反向事实。','down')
    add('cold_heating_demand','隔离假设：欧洲官方天气预报将未来七天气温下修4摄氏度，预计采暖天然气需求上升15%。本窗口首次发布，未给供应恢复或其他反向事实。','up')
    add('negated_outage','隔离假设：运营方否认供应中断，出口计量确认流量未变；传闻中的意外停产未发生。没有提供先前溢价、报价变化或其他欧洲供需消息。','flat',close=None)
    row=add('cumulative_shock_then_weak',weak,'up')
    shock={'id':'diagnostic-earlier','title':'欧洲供气实际下降','summary':up,'source':'diagnostic_fixture','published_at':'2026-10-20T11:00:00+00:00','first_seen_at':'2026-10-20T11:01:00+00:00','timestamp_precision':'exact','version':1,'url':''}
    for key in ['original_context','candidate_context']:row[key]['window_news'].insert(0,copy.deepcopy(shock))
    add('weak_news_observed_market_up',weak,'flat',price=True,close='up')
    add('supplied_conflicting_mechanisms','隔离假设：欧洲天然气管道输气实际减少20mcm/d，同时新增加的LNG实际再气化流量为30mcm/d，两者均预计持续七天。需求没有给出变化。请分别判断供给约束和供应增加机制。',None)
    for name,status,start,end,title in [
        ('future_planned_notice','Active','2027-05-22T06:00:00+02:00','2027-05-25T06:00:00+02:00','Planned'),
        ('dismissed_notice_not_verified_recovery','Dismissed','2026-10-20T06:00:00+02:00','2026-10-21T06:00:00+02:00','Unplanned'),
    ]:
        row=add(name,f'隔离假设运营公告。Technical capacity: 100 mcm/d; available: 80; unavailable: 20. No independent actual flow measurement or recovery confirmation is provided.',None)
        for key in ['original_context','candidate_context']:
            n=row[key]['current_news']
            n.update(source='gassco',title=f'Gassco test: {title} Gas production field unavailability ({status})',
                     event_start=start,event_end=end,event_status=status)
            row[key]['window_news']=[copy.deepcopy(n)]
    # Real replay inputs retain their original as-of, prices and article versions.
    rows=json.loads((ROOT/'.runtime/flat_audit_rows.json').read_text())
    from ttf_news.store import Store
    from contextlib import contextmanager
    class ReadOnlyStore(Store):
        def __init__(self,path):self.path=path
        @contextmanager
        def connection(self):
            c=sqlite3.connect(f'file:{self.path}?mode=ro',uri=True);c.row_factory=sqlite3.Row
            try:yield c
            finally:c.close()
    store=ReadOnlyStore(ROOT/'data/backups/pre-logic-repair-20261020.sqlite3')
    for prefix,name in [('a088eee3','real_nyhamna'),('362a0ad9','real_eni')]:
        row=next(r for r in rows if r['id'].startswith(prefix));old=copy.deepcopy(row['input_snapshot']);new=enrich_real_replay(store,old)
        focus=[new['current_news']]+new['window_news']
        query=' '.join(str(n.get('title',''))+' '+str(n.get('summary','')) for n in focus)
        new['historical_events']=store.retrieve_events(query,new['as_of'],exclude_news_ids=[n['id'] for n in focus if n.get('id')])
        new['price_reference']['market_baseline']=market_baseline(new['price_reference'],new['session'],new['as_of'])
        cases.append({'id':name,'synthetic':False,'source_analysis_id':row['id'],'original_context':old,'candidate_context':new,'expected_news':None})
    original=json.dumps(cases,ensure_ascii=False,indent=2).encode('utf-8')
    save_prepared(add_flat_baseline_controls(cases),original)

class TracedAnalyst(NewsAnalyst):
    async def _request(self,system,data,detail):
        entry={'system':system,'input':copy.deepcopy(data)}
        guard=getattr(self,'paused_live_guard',None)
        if guard is not None:entry['live_queue_policy']=await guard()
        self.trace.append(entry)
        raw=await super()._request(system,data,detail)
        raw=copy.deepcopy(raw)
        entry['thinking_char_count']=len(raw.get('message',{}).get('thinking',''))
        raw.get('message',{}).pop('thinking',None)
        entry['response']=raw
        return raw


def analyst_options(options):
    """The frozen original engine must never receive a new constructor option."""
    kwargs={'reasoning':options.reasoning} if options.reasoning else {}
    if options.engine=='candidate' and options.semantic_high_scope is not None:
        kwargs['semantic_high_scope']=options.semantic_high_scope
    return kwargs


def analyst_configuration(analyst):
    """Record effective instance settings, including an implicit default scope."""
    return {name:getattr(analyst,name,None) for name in
            ('model','reasoning','semantic_high_retry','semantic_high_scope')}


async def run():
    cases,fixture=load_case_file(args.case_file,args.engine,args.case)
    analyst=TracedAnalyst(**analyst_options(args));health=await analyst.health()
    if not health['available']:raise RuntimeError(health)
    run_dir=OUT/(args.engine+'-'+args.run_id+('-detail' if args.detail else ''))
    run_dir.mkdir(exist_ok=True)
    sources={str(p.relative_to(source)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (source/'ttf_news').glob('*.py')}
    for case in cases:
        if args.case and case['id'] not in args.case:continue
        path=run_dir/(case['id']+'.json')
        if path.exists():print('EXISTS',path.name,flush=True);continue
        # Default yields to live work. The opt-in bypass proves this project's
        # inference worker is paused, and repeats that proof before each call.
        queue_policy=await wait_for_live_queue(args.allow_paused_live_queue)
        analyst.paused_live_guard=verify_paused_local_health if args.allow_paused_live_queue else None
        analyst.trace=[]
        ctx=case[args.engine+'_context']
        record={'case':case['id'],'engine':args.engine,'prompt_version':PROMPT_VERSION,'synthetic':case['synthetic'],
                'started_at':datetime.now(timezone.utc).isoformat(),'source_hashes':sources,'model':health,
                'model_call_configuration':analyst_configuration(analyst),
                'case_fixture':fixture,'live_queue_policy':queue_policy,
                'input_snapshot':copy.deepcopy(ctx),
                'input_sha256':hashlib.sha256(json.dumps(ctx,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
                'expected_news':case.get('expected_news')}
        if 'expected_close' in case:record['expected_close']=case['expected_close']
        if 'expected_close_sign' in case:record['expected_close_sign']=case['expected_close_sign']
        if 'requires_original_comparison' in case:record['requires_original_comparison']=case['requires_original_comparison']
        for key in ('expected_status','expected_close_status','expected_evidence_status','required_explicit_future_well_action','source_context_provenance'):
            if key in case:record[key]=copy.deepcopy(case[key])
        print('START',args.engine,case['id'],'detail',args.detail,flush=True)
        try:
            result=await analyst.analyze(ctx,args.detail);record['result']=result
            news=result['news_impact']['direction'];close=result['close_forecast'].get('direction')
            expected=case.get('expected_news')
            # Supply/demand controls identify pressure sign, not a calibrated
            # future price magnitude. Do not tune away either extreme class.
            accepted={'up':{'up','extreme_up'},'down':{'down','extreme_down'}}.get(expected,{expected})
            passed=(expected is None or news in accepted) and ('expected_close' not in case or close==case['expected_close'])
            if 'expected_close_sign' in case:
                accepted_close={'up':{'up','extreme_up'},'down':{'down','extreme_down'}}[case['expected_close_sign']]
                passed=passed and close in accepted_close and result['close_forecast'].get('evidence_status')=='forecast'
            expected_status=case.get('expected_close_status',case.get('expected_evidence_status',case.get('expected_status')))
            if expected_status is not None:passed=passed and result['close_forecast'].get('evidence_status')==expected_status
            future_action=explicit_future_well_action_check(result,case)
            record['explicit_future_well_action']=future_action
            passed=passed and future_action['passed']
            record['contract_passed']=passed
            print('RESULT',case['id'],'news',news,'close',close,'pass',passed,flush=True)
        except Exception as exc:
            record['error']=str(exc);record['audit']=getattr(exc,'audit',{});record['contract_passed']=False
            import traceback
            record['traceback']=traceback.format_exc()
            print('ERROR',case['id'],str(exc)[:450],flush=True)
        record['attempts']=analyst.trace
        record['finished_at']=datetime.now(timezone.utc).isoformat()
        dump(path,record)
    print('RUN_COMPLETE',run_dir,flush=True)

if __name__=='__main__':
    if args.prepare:prepare()
    else:asyncio.run(run())
