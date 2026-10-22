"""Separate diagnostic inputs and guarded local queue maintenance, no inference."""
import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path

import httpx
import pytest


SPEC=importlib.util.spec_from_file_location('ttf_anchor_comparison_cli',Path(__file__).resolve().parents[1]/'scripts/validate-news-logic.py')
cli=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)
NOW=datetime(2026,10,21,10,tzinfo=timezone.utc)
VERSION='ttf-news-standalone-test-current'


def case(identifier='isolated'):
    return {'id':identifier,'synthetic':True,'expected_news':None,
            'candidate_context':{'current_news':{'id':'synthetic-'+identifier}}}


def health(**changes):
    return {'status':'ok','prompt_version':VERSION,'inference':{'enabled':False},
            'server_pid':12345,'server_time':NOW.isoformat(),**changes}


def test_options_leave_live_priority_and_original_fixture_as_defaults():
    options=cli.parser.parse_args([])
    assert options.case_file is None and options.allow_paused_live_queue is False
    assert cli.analyst_options(options)=={}


def test_explicit_case_file_reads_without_modifying_original_or_selected_input(tmp_path,monkeypatch):
    original=tmp_path/'comparison_cases.json';original.write_text('["frozen original bytes"]')
    selected=tmp_path/'anchor.json';selected.write_text(json.dumps([case()]))
    before=(original.read_bytes(),selected.read_bytes());monkeypatch.setattr(cli,'OUT',tmp_path)
    rows,identity=cli.load_case_file(selected)
    assert rows==[case()] and identity['explicit_case_file']
    assert identity['sha256']==cli.hashlib.sha256(before[1]).hexdigest()
    assert (original.read_bytes(),selected.read_bytes())==before


@pytest.mark.parametrize('rows',[[case(),case()],[case('../escape')],[],{'cases':[case()]}])
def test_separate_case_file_rejects_duplicate_or_unsafe_identifiers(tmp_path,rows):
    path=tmp_path/'bad.json';path.write_text(json.dumps(rows))
    with pytest.raises(ValueError):cli.load_case_file(path)


def test_custom_case_file_cannot_enter_original_prepare_mutation_path():
    with pytest.raises(SystemExit):cli.parser.parse_args(['--prepare','--case-file','isolated.json'])


def test_requested_case_must_exist_before_model_health_or_execution(tmp_path):
    path=tmp_path/'cases.json';path.write_text(json.dumps([case()]))
    with pytest.raises(ValueError,match='do not exist'):cli.load_case_file(path,requested=['typo'])


@pytest.mark.parametrize('change',[
    {'inference':None},{'inference':{}},{'inference':{'enabled':True}},
    {'inference':{'enabled':0}},{'prompt_version':'old'},
    {'server_pid':None},{'server_time':None},{'status':'model_unavailable'},
    {'server_time':(NOW-timedelta(seconds=31)).isoformat()},
    {'server_time':(NOW+timedelta(seconds=6)).isoformat()},
])
def test_paused_override_refuses_missing_enabled_old_or_noncurrent_health(change):
    with pytest.raises(RuntimeError):cli.validate_paused_live_health(health(**change),VERSION,NOW)


def test_paused_override_requires_literal_false_and_current_version():
    payload=health();before=copy.deepcopy(payload)
    audit=cli.validate_paused_live_health(payload,VERSION,NOW)
    assert audit['inference_enabled'] is False and audit['server_pid']==12345
    assert payload==before


@pytest.mark.asyncio
async def test_pause_check_uses_only_fixed_local_health_http_endpoint():
    requested=[]
    def handle(request):
        requested.append(str(request.url))
        return httpx.Response(200,json=health())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        audit=await cli.verify_paused_local_health(client,VERSION,NOW)
    assert requested==['http://127.0.0.1:8765/api/health']
    assert audit['mode']=='inference_paused_verified'


@pytest.mark.asyncio
async def test_verified_pause_skips_queue_without_reading_or_waiting_jobs(monkeypatch):
    def no_database():raise AssertionError('Paused override should not inspect queued jobs')
    async def verified():return {'mode':'inference_paused_verified'}
    monkeypatch.setattr(cli,'pending_live_jobs',no_database)
    monkeypatch.setattr(cli,'verify_paused_local_health',verified)
    assert (await cli.wait_for_live_queue(True))['mode']=='inference_paused_verified'


@pytest.mark.asyncio
async def test_default_still_waits_for_live_queue_and_never_requests_override(monkeypatch):
    pending=iter([2,1,0]);waits=[]
    monkeypatch.setattr(cli,'pending_live_jobs',lambda:next(pending))
    async def sleep(seconds):waits.append(seconds)
    async def forbidden():raise AssertionError('Default must retain live priority')
    monkeypatch.setattr(cli.asyncio,'sleep',sleep)
    monkeypatch.setattr(cli,'verify_paused_local_health',forbidden)
    assert await cli.wait_for_live_queue()=={'mode':'live_priority','pending_jobs_at_start':0}
    assert waits==[5,5]


@pytest.mark.asyncio
async def test_pause_is_rechecked_before_each_serial_model_request(monkeypatch):
    requests=[];checks=[]
    async def fake_request(self,system,data,detail):
        requests.append(system)
        return {'message':{'content':'{}'}}
    async def guard():
        checks.append(True)
        if len(checks)>1:raise RuntimeError('inference enabled again')
        return {'inference_enabled':False}
    monkeypatch.setattr(cli.NewsAnalyst,'_request',fake_request)
    analyst=cli.TracedAnalyst();analyst.trace=[];analyst.paused_live_guard=guard
    await analyst._request('first',{},False)
    with pytest.raises(RuntimeError,match='enabled again'):await analyst._request('retry',{},False)
    assert requests==['first'] and len(checks)==2 and len(analyst.trace)==1


def test_current_live_version_is_read_from_product_not_frozen_engine(monkeypatch,tmp_path):
    (tmp_path/'ttf_news').mkdir();(tmp_path/'ttf_news/prompts.py').write_text('PROMPT_VERSION="current-local"\n')
    monkeypatch.setattr(cli,'ROOT',tmp_path)
    assert cli.current_product_prompt_version()=='current-local'
