from datetime import date, datetime, timedelta, timezone
import json
import pytest
from fastapi.testclient import TestClient

from ttf_news.calendar import cutoff,session_at,session_for_day,parse_time
from ttf_news.labels import actual_label,threshold
from ttf_news.store import Store
from ttf_news.context import build_context
from ttf_news.api import create_app
from ttf_news.export import render_export


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path/"test.sqlite3")


def item(**changes):
    return {"source":"test","url":"https://example.org/gas?a=1&utm_source=test","title":"Norwegian gas outage","summary":"Planned maintenance reduces gas capacity.","published_at":"2026-10-14T09:00:00+00:00","published_date":"2026-10-14","timestamp_precision":"exact","timestamp_source":"rss","first_seen_at":"2026-10-14T09:01:00+00:00",**changes}


def test_settlement_dst_boundary_weekend_early():
    assert cutoff(date(2026,10,14)).hour==15
    assert cutoff(date(2026,11,2)).hour==16
    end=cutoff(date(2026,10,14))
    assert session_at(end-timedelta(microseconds=1))["trade_date"]=="2026-10-14"
    assert session_at(end)["trade_date"]=="2026-10-15"
    assert session_at("2026-10-17T12:00:00+00:00")["trade_date"]=="2026-10-19"
    assert cutoff(date(2026,12,24)).hour==13
    assert session_at("2026-12-25T12:00:00+00:00")["trade_date"]=="2026-12-28"
    with pytest.raises(ValueError):
        parse_time("2026-10-14T17:15:00")


def test_easter_closed_days():
    assert session_at("2026-04-03T10:00:00+00:00")["trade_date"]=="2026-04-07"
    assert session_at("2027-03-29T10:00:00+00:00")["trade_date"]=="2027-03-30"
    assert session_at('2026-05-25T10:00:00+00:00')['trade_date']=='2026-05-25'  # Schedule C keeps settlement unchanged.
    assert cutoff(date(2026,11,27)).hour==16  # Schedule B closes trading early, not settlement.
    assert session_at('2027-12-27T10:00:00+00:00')['trade_date']=='2027-12-27'


def test_date_only_live_is_observed_background_without_fabricated_window(store):
    from unittest.mock import patch
    with patch('ttf_news.store.iso',return_value='2026-10-14T09:01:00+00:00'):
        n=store.upsert_news(item(published_at=None,timestamp_precision='date',ingestion_kind='live'))
    ctx=build_context(store,as_of='2026-10-14T10:00:00+00:00')
    assert store.get_news(n['id'])['trade_date'] is None
    assert store.window_news('2026-10-14','2026-10-14T10:00:00+00:00')==[]
    assert ctx['window_news'][0]['window_membership']=='first_observed_in_window_publication_window_unknown'
    assert ctx['coverage']['observed_imprecise_count']==1
    assert ctx['daily_aggregate']['article_count']==0
    assert build_context(store,as_of='2026-10-14T09:00:00+00:00')['window_news']==[]


def test_dedup_revision_and_metadata_correction(store):
    first=store.upsert_news(item(published_at=None,timestamp_precision="date"))
    assert first["trade_date"] is None
    repeated=store.upsert_news(item(published_at=None,timestamp_precision="date"))
    assert not repeated["_created"] and not repeated["_revised"]
    repaired=store.upsert_news(item())
    assert repaired["_revised"] and repaired["version"]==2
    assert repaired["first_seen_at"]==first["first_seen_at"]
    assert repaired["trade_date"]=="2026-10-14"
    assert store.counts()["news"]==1
    assert "utm_source" not in repaired["url"]


def test_real_time_versions_not_latest_future_revision(store):
    from unittest.mock import patch
    with patch('ttf_news.store.iso',return_value="2026-10-14T09:01:00+00:00"):
        first=store.upsert_news(item())
    with patch('ttf_news.store.iso',return_value="2026-10-14T11:00:00+00:00"):
        store.upsert_news(item(summary="Restart completed. Supply restored."))
    then=store.window_news("2026-10-14","2026-10-14T10:00:00+00:00")
    now=store.window_news("2026-10-14","2026-10-14T12:00:00+00:00")
    assert len(then)==1 and "maintenance" in then[0]["summary"]
    assert "Restart" in now[0]["summary"]


def test_source_failure_kept_distinct_from_empty_success(store):
    store.set_source_state("test",{"status":"failed","last_error":"timeout"})
    store.set_source_state("other",{"status":"ok","article_count":0})
    states={x['id']:x for x in store.coverage()['sources']}
    assert states['test']['status']=='failed'
    assert states['other']['article_count']==0


def test_task_priority_detail_cache_and_recovery(store):
    context={"session":{"trade_date":"2026-10-14"}}
    live=store.create_analysis(context,"live",priority=10)
    manual=store.create_analysis(context,"manual",priority=0)
    job=store.claim_job()
    assert job['analysis_id']==manual['id']
    store.recover_jobs()
    job=store.claim_job()
    assert job['analysis_id']==manual['id']
    store.finish_job(job,result={"close_forecast":{"direction":"up"}})
    assert store.request_detail(manual['id'])['status']=='queued'
    assert store.request_detail(manual['id'])['status']=='queued'
    with store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM jobs WHERE kind='detail'").fetchone()[0]==1
    assert store.claim_job()['analysis_id']==live['id']


def test_sse_ids_replay(store):
    store.emit("news_created",{"id":"a"})
    store.emit("analysis_completed",{"id":"b"})
    events=store.stream_since(0)
    assert store.stream_since(events[0]['id'])==[events[1]]


def test_threshold_edges_and_short_history():
    assert threshold([1.0]*59) is None
    assert threshold([1.0]*60)==0.30
    assert actual_label(0.30,2.0)=='flat'
    assert actual_label(-0.31,None) is None
    assert actual_label(2.0,2.0)=='extreme_up'
    assert actual_label(-1.0,2.0)=='down'


def test_future_price_and_other_contract_excluded(store):
    from ttf_news.calendar import next_day
    day=date(2026,7,1)
    for i in range(65):
        store.upsert_price({"contract":"nov26","delivery_month":"2026-11-01","day":day.isoformat(),"value":30+i%3,"price_type":"reference","known_at":"2026-10-14T00:00:00+00:00"})
        day=next_day(day)
    store.upsert_price({"contract":"nov26","delivery_month":"2026-11-01","day":"2026-10-14","value":1000,"price_type":"reference","known_at":"2026-10-14T00:00:00+00:00"})
    store.upsert_price({"contract":"dec26","delivery_month":"2026-12-01","day":"2026-10-13","value":1e6,"price_type":"reference","known_at":"2026-10-14T00:00:00+00:00"})
    context=build_context(store,as_of="2026-10-14T10:00:00+00:00")
    assert context['price_reference']['contract']=='nov26'
    assert context['price_reference']['previous_price']<100
    assert context['price_reference']['extreme_threshold']<10
    assert not context['price_reference']['verified']


def test_volatility_window_rejects_missing_trading_day(store):
    from ttf_news.context import price_context
    from ttf_news.calendar import next_day
    day=date(2026,7,1)
    for i in range(70):
        if i!=50:
            store.upsert_price({"contract":"nov26","delivery_month":"2026-11-01","day":day.isoformat(),"value":30+i%3,"price_type":"reference","known_at":"2026-10-14T00:00:00+00:00"})
        day=next_day(day)
    result=price_context(store,"2026-10-14T10:00:00+00:00")
    assert result['extreme_threshold'] is None
    assert result['sigma60'] is None
    assert any('缺口或不足' in flag for flag in result['risk_flags'])


def test_event_reaction_selects_near_month_and_excludes_unfinished_days(store):
    from unittest.mock import patch
    from ttf_news.evaluation import enrich_outcomes
    store.upsert_event({'id':'outage','source':'test','title':'Norway outage','event_date':'2026-10-09'})
    for contract,month,values in (('far','2027-02-01',[10,1000,2000,3000]),('near','2026-11-01',[40,41,42,9000])):
        for day,value in zip(('2026-10-08','2026-10-09','2026-10-12','2026-10-14'),values):
            store.upsert_price({'contract':contract,'delivery_month':month,'day':day,'value':value,'price_type':'reference',
                                'known_at':day+'T15:15:00+00:00'})
    with patch('ttf_news.evaluation.iso',return_value='2026-10-14T04:00:00+00:00'):
        enrich_outcomes(store)
    reaction=store.events()[0]['outcomes']['1d']
    assert reaction['contract']=='near' and reaction['change']==1
    assert reaction['end_date']=='2026-10-12'
    assert '3d' not in store.events()[0]['outcomes']


class StubAnalyst:
    async def health(self):
        return {"available":True,"model":"test"}


def test_manual_api_does_not_pollute_news_or_events(store):
    app=create_app(store,StubAnalyst(),workers=False)
    with TestClient(app) as client:
        # Lifespan may import available legacy archives; manual inputs must
        # preserve whichever formal dataset was present before submission.
        store.upsert_news(item(title='Existing formal news fixture'))
        store.upsert_event({'id':'existing-formal-event','source':'test',
                            'title':'Existing formal event fixture','event_date':'2026-10-14'})
        baseline=store.counts()
        response=client.post('/api/manual/analyze',json={"text":"手动情景：天然气供应意外中断，但天气温和。"})
        assert response.status_code==202
        assert store.counts()['news']==baseline['news']
        assert store.counts()['events']==baseline['events']
        aid=response.json()['id']
        analysis=client.get('/api/analyses/'+aid+'?include_input=true').json()
        assert analysis['kind']=='manual'
        assert analysis['input_snapshot']['current_news']['published_at'] is None
        assert client.post('/api/analyses/'+aid+'/detail').status_code==409
        assert client.post('/api/manual/analyze',json={"text":"too short"}).status_code==422
        assert client.post('/api/manual/analyze',json={"text":"合法长度的新闻输入仅用于验证本地来源保护。"},headers={"Origin":"https://bad.example"}).status_code==403


def test_export_provenance_and_formula_guard(store):
    store.upsert_news(item(title='=IMPORTXML("bad")'))
    content,_=render_export(store,'news.csv')
    assert "'=IMPORTXML" in content.decode()
    content,_=render_export(store,'news.jsonl')
    record=json.loads(content.decode().splitlines()[0])
    assert record['timestamp_precision']=='exact'
    assert record['first_seen_at']
