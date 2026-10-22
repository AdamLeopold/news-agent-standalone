"""CPU-only publisher authority and scheduling regressions; disposable Store only."""
import asyncio
import copy
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import pytest

from ttf_news import ingest, runtime
from ttf_news.store import Store

UTC = timezone.utc


def test_body_dom_excludes_presentation_preserves_real_dates_negation_and_numbers():
    body = 'On October 22, 2026, gas supply did not increase by 40 mcm/d; the outage remains unresolved.'
    doc = '<article><header><p>October 22, 2026 · 6 hours ago</p><p>Author Long Publisher Name</p></header><figure><figcaption><p>Image LNG capacity 200 mcm/d</p></figcaption></figure><div class="article-content"><p>'+body+'</p><p>At 14:00 the operator did not confirm a restart; these are actual body facts.</p></div></article>'
    item = ingest.extract_article(doc, 'https://publisher.test/gas', {'id':'eia'}, historical=False, fallback_title='Natural gas supply')
    assert body in item['fact_excerpt']
    assert '14:00' in item['fact_excerpt'] and 'did not confirm' in item['fact_excerpt']
    assert 'hours ago' not in item['fact_excerpt'] and 'Image LNG' not in item['fact_excerpt'] and 'Author' not in item['fact_excerpt']
    assert ingest.extract_article(doc.replace('6 hours ago','2 minutes ago'), item['url'], {'id':'eia'}, historical=False, fallback_title=item['title'])['fact_excerpt'] == item['fact_excerpt']


@pytest.mark.asyncio
async def test_api_failure_rss_discovers_new_url_without_downgrading_known_api(tmp_path):
    db=Store(tmp_path/'authority.sqlite3');now=datetime.now(UTC)-timedelta(hours=1)
    url='https://offshore-energy.biz/'+now.strftime('%Y/%m/%d')+'/known-gas'
    old=ingest.news_item('offshore_energy',url,'Natural gas supply','Known summary',now.replace(microsecond=480000).isoformat(),historical=False,timestamp_source='publisher API publishedAt',updated_at=now.isoformat())
    ingest.attach_fact_excerpt(old,['Actual gas supply to Europe did not increase by 40 mcm/d.'],method='public_publisher_payload_paragraphs')
    saved=db.upsert_news(old)
    new_url='https://offshore-energy.biz/'+now.strftime('%Y/%m/%d')+'/new-lng'
    rss='<rss><channel>'+''.join('<item><title>Natural gas supply</title><link>'+u+'</link><description>Short RSS description</description><pubDate>'+now.replace(microsecond=0).strftime('%a, %d %b %Y %H:%M:%S +0000')+'</pubDate></item>' for u in (url,new_url))+'</channel></rss>'
    calls=[]
    def handler(request):
        calls.append(request.url.path)
        if request.url.path=='/api/articles': return httpx.Response(503,text='publisher API unavailable')
        if request.url.path=='/feed': return httpx.Response(200,text=rss)
        return httpx.Response(200,text='<article><div class="article-content"><p>New LNG capacity is planned, but production has not started.</p></div></article>')
    source={'id':'offshore_energy','feed_url':'https://offshore-energy.biz/feed','api_url':'https://offshore-energy.biz/api/articles'}
    with patch.object(ingest.asyncio,'sleep',return_value=None):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            fetch=ingest.Fetcher(client,source,{},db,False,[])
            result=await ingest.enrich_live_facts(fetch,await ingest.ingest_offshore(fetch,now.date()))
    known=next(x for x in result if x['url']==url); new=next(x for x in result if x['url']==new_url)
    repeated=db.upsert_news(known)
    assert repeated['version']==saved['version'] and not repeated['_revised']
    for key in ('published_at','updated_at','fact_excerpt','entities'): assert known[key]==saved[key]
    assert new['fact_excerpt'] and calls.count('/api/articles')==1
    assert fetch.failures and fetch.state['last_api_error'] and fetch.state['live_transport']=='rss_fallback'


def test_same_instant_api_and_missing_fields_preserve_facts_real_body_change_revises(tmp_path):
    db=Store(tmp_path/'revisions.sqlite3')
    old=ingest.news_item('offshore_energy','https://offshore-energy.biz/gas','Natural gas update','Gas source summary','2026-10-22T10:00:00.000Z',historical=False,timestamp_source='publisher API publishedAt',updated_at='2026-10-22T11:00:00Z')
    ingest.attach_fact_excerpt(old,['European gas supply did not recover; flows are still constrained by 40 mcm/d.'],method='public_publisher_payload_paragraphs')
    first=db.upsert_news(old)
    incoming=ingest.news_item('offshore_energy',old['url'],old['title'],old['summary'],'2026-10-22T12:00:00+02:00',historical=False,timestamp_source='publisher API publishedAt')
    carried=ingest._preserve_publisher_authority(incoming,first)
    assert carried['published_at']==first['published_at']
    assert carried['updated_at']==first['updated_at'] and carried['fact_excerpt']==first['fact_excerpt'] and carried['entities']==first['entities']
    assert not db.upsert_news(carried)['_revised']
    changed=copy.deepcopy(carried)
    ingest.attach_fact_excerpt(changed,['European actual gas supply recovered by 40 mcm/d after the operator confirmed restart.'],method='public_publisher_payload_paragraphs')
    assert db.upsert_news(changed)['_revised']
    assert db.previous_news_version(first['id'],2,datetime.now(UTC).isoformat())['data']['fact_excerpt']==first['fact_excerpt']


@pytest.mark.parametrize('substantive,window_changed,expected', [(False,False,0),(True,False,1),(False,True,1)])
def test_collection_dispatch_skips_only_compared_metadata_revision(monkeypatch,substantive,window_changed,expected):
    from types import SimpleNamespace
    import ttf_news.ingest as active_ingest
    now=datetime.now(UTC);day=__import__('ttf_news.calendar',fromlist=['session_at']).session_at()['trade_date']
    news={'id':'known','version':2,'_revised':True,'source':'offshore_energy','ingestion_kind':'live','published_at':now.isoformat(),'trade_date':day}
    context={'as_of':now.isoformat(),'session':{'trade_date':day},'current_news':{**news,'revision_delta':{'substantive_change':substantive,'comparison_status':'compared'}}}
    created=[]
    store=SimpleNamespace(window_news=lambda *a:[],observed_imprecise_news=lambda *a:[],latest_analysis=lambda **k:{'status':'queued'},previous_news_version=lambda *a:{'data':{'trade_date':'1999-01-01' if window_changed else day}},create_analysis=lambda *a,**k:created.append((a,k)),metric=lambda *a:None)
    async def poll(_): return {'created':[],'revised':[news]}
    async def stop(_): raise asyncio.CancelledError
    monkeypatch.setattr(active_ingest,'poll_sources',poll)
    monkeypatch.setattr(runtime,'build_context',lambda *a,**k:copy.deepcopy(context))
    monkeypatch.setattr(runtime,'update_event_memory',lambda *a:None)
    monkeypatch.setattr(runtime.asyncio,'sleep',stop)
    with pytest.raises(asyncio.CancelledError): asyncio.run(runtime.collection_worker(store))
    assert len(created)==expected


def test_api_complete_cursor_survives_partial_rss_success_and_recovers_old_updated_article(tmp_path,monkeypatch):
    now=datetime.now(UTC);old_complete=now-timedelta(days=5);published=now-timedelta(days=30)
    db=Store(tmp_path/'cursor.sqlite3');source={'id':'offshore_energy','name':'Offshore','feed_url':'https://publisher.test/feed','api_url':'https://publisher.test/api/articles'}
    db.set_source_state('offshore_energy',{'last_complete_api_success_at':old_complete.isoformat(),'last_success_at':now.isoformat()})
    api_calls=[];attempts=0
    def handler(request):
        nonlocal attempts
        if request.url.path=='/api/articles':
            attempts+=1;api_calls.append(request.url)
            if attempts<=2:return httpx.Response(503,text='API outage; RSS discovery still available')
            return httpx.Response(200,json={'docs':[{'id':1,'title':'Old gas article with genuine body revision','slug':'old-gas','publishedAt':published.isoformat(),'updatedAt':now.isoformat(),'_status':'published','meta':{'description':'A genuine new source body observation.'},'content':[{'type':'paragraph','children':[{'type':'text','text':'European actual gas supply increased by 40 mcm/d after a confirmed restart.'}]}]}],'hasNextPage':False,'totalDocs':1})
        return httpx.Response(200,text='<rss><channel><item><title>Ocean shipping update</title><link>https://publisher.test/other</link><description>Other maritime activities.</description></item></channel></rss>')
    original=httpx.AsyncClient
    monkeypatch.setattr(ingest,'load_sources',lambda:[source])
    monkeypatch.setattr(ingest.httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handler)))
    async def no_sleep(_):pass
    monkeypatch.setattr(ingest.asyncio,'sleep',no_sleep)
    for _ in range(2):
        result=asyncio.run(ingest.poll_sources(db));state=db.get_source_state('offshore_energy')
        assert result['sources'][0]['status']=='partial' and state['last_api_error']
        assert state['last_complete_api_success_at']==old_complete.isoformat()
        assert datetime.fromisoformat(state['last_success_at'])>old_complete
    result=asyncio.run(ingest.poll_sources(db));state=db.get_source_state('offshore_energy')
    expected=(old_complete-timedelta(days=1)).date().isoformat()+'T00:00:00.000Z'
    for url in api_calls:
        assert url.params['where[and][0][or][0][publishedAt][greater_than_equal]']==expected
        assert url.params['where[and][0][or][1][updatedAt][greater_than_equal]']==expected
    assert result['total_created']==1 and state['status']=='ok'
    assert datetime.fromisoformat(state['last_complete_api_success_at'])>=now
    assert state['api_recovery_from'] is None
    assert db.news()[0]['published_at']==published.isoformat() and '40 mcm/d' in db.news()[0]['fact_excerpt']


def test_failed_initial_api_cursor_latches_and_bad_date_rows_do_not_advance(tmp_path,monkeypatch):
    now=datetime.now(UTC);db=Store(tmp_path/'invalid-slice.sqlite3')
    source={'id':'offshore_energy','name':'Offshore','feed_url':'https://publisher.test/feed','api_url':'https://publisher.test/api/articles'}
    db.set_source_state('offshore_energy',{'api_recovery_from':(now-timedelta(days=8)).date().isoformat(),'last_success_at':now.isoformat()})
    calls=[]
    def handler(request):
        if request.url.path=='/api/articles':
            calls.append(request.url)
            return httpx.Response(200,json={'docs':[{'id':1,'title':'Old gas report outside both filter arms','slug':'old-gas','publishedAt':(now-timedelta(days=30)).isoformat(),'updatedAt':(now-timedelta(days=20)).isoformat(),'_status':'published','meta':{'description':'Not a valid incremental result.'}}],'hasNextPage':False})
        return httpx.Response(200,text='<rss><channel><item><title>Ocean shipping update</title><link>https://publisher.test/other</link><description>Other maritime activities.</description></item></channel></rss>')
    original=httpx.AsyncClient
    monkeypatch.setattr(ingest,'load_sources',lambda:[source]);monkeypatch.setattr(ingest.httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handler)))
    async def no_sleep(_):pass
    monkeypatch.setattr(ingest.asyncio,'sleep',no_sleep)
    result=asyncio.run(ingest.poll_sources(db));state=db.get_source_state('offshore_energy')
    assert result['sources'][0]['status']=='partial' and state['last_api_error']
    assert not state.get('last_complete_api_success_at') and state['api_recovery_from']==(now-timedelta(days=8)).date().isoformat()
    assert calls[0].params['where[and][0][or][1][updatedAt][greater_than_equal]'].startswith(state['api_recovery_from'])
    assert not db.news()
