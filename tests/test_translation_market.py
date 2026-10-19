import copy
import json
from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from ttf_news.store import Store
from ttf_news.translation import prepare_translation, restore_translation, source_hash
from ttf_news.market import parse_intraday, parse_quote, save_quote, known_quote, market_view, refresh_market


def result():
    return {'news_impact': {'direction': 'up', 'confidence': .7, 'contextual_news_score': .8,
                            'summary': '供应减少 4.5 bcm，预计上涨 [N1]，并非已计价。'},
            'close_forecast': {'direction': 'flat', 'confidence': '低', 'basis': '截至2026-10-19，尚未确认。'},
            'risk_flags': ['价格参考 https://example.com/ref 尚未验证。'],
            'references': [{'id': 'N1', 'title': 'Original source', 'url': 'https://example.com'}],
            '_meta': {'as_of': '2026-10-19T08:00:00+00:00', 'source_index': []}}


def translated_values(texts):
    import re
    return {name: 'Unconfirmed supply pressure. '+ ' '.join(re.findall(r'__KEEP_\d+__', text)) for name, text in texts.items()}


def test_english_translation_preserves_judgments_scores_numbers_and_citations():
    original = result()
    before = copy.deepcopy(original)
    texts, keep = prepare_translation(original)
    output = restore_translation(original, translated_values(texts), texts, keep)
    assert original == before
    assert output['news_impact']['direction']=='up'
    assert output['news_impact']['contextual_news_score']==.8
    assert output['close_forecast']['direction']=='flat'
    assert output['close_forecast']['confidence']=='低'
    assert '4.5' in output['news_impact']['summary'] and '[N1]' in output['news_impact']['summary']
    assert output['references']==before['references']
    assert output['_meta']['translation_source_sha256']==source_hash(before)
    assert output['_meta']['output_language']=='en'


@pytest.mark.parametrize('defect', ['missing', 'chinese', 'placeholder', 'invented_number'])
def test_invalid_translation_is_an_error_instead_of_a_forecast(defect):
    original=result(); texts,keep=prepare_translation(original)
    translated=translated_values(texts); first=next(iter(translated))
    if defect=='missing': translated.pop(first)
    elif defect=='chinese': translated[first]+='供应'
    elif defect=='placeholder': translated[first]=translated[first].replace('__KEEP_0__','')
    else: translated[first]+=' Expected price 150.'
    with pytest.raises(ValueError):
        restore_translation(original,translated,texts,keep)


def test_translation_cache_recovery_retry_and_frozen_original_are_independent(tmp_path):
    store=Store(tmp_path/'cache.db')
    analysis=store.create_analysis({'session':{'trade_date':'2026-10-19'}},'live',priority=10)
    job=store.claim_job(); store.finish_job(job,result=result())
    with store.connection() as c: c.execute('UPDATE analyses SET frozen=1 WHERE id=?',(analysis['id'],))
    before=store.analysis(analysis['id'],include_input=True)
    first=store.request_translation(analysis['id'])
    assert store.request_translation(analysis['id'])['id']==first['id']
    assert store.cached_translation(analysis['id'])['id']==first['id']
    job=store.claim_job()
    assert job['kind']=='translation' and job['priority']==12
    assert store.translation_record(first['id'])['status']=='running'
    store.recover_jobs(); assert store.translation_record(first['id'])['status']=='queued'
    job=store.claim_job(); store.finish_job(job,error='Translation validation failed')
    assert store.request_translation(analysis['id'])['status']=='failed'
    store.request_translation(analysis['id'],retry=True)
    job=store.claim_job(); store.finish_job(job,result={'_meta':{'output_language':'en'}})
    assert store.cached_translation(analysis['id'])['status']=='completed'
    assert store.analysis(analysis['id'],include_input=True)==before
    with store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM jobs WHERE kind='translation' AND status='queued'").fetchone()[0]==0


def test_api_serves_cached_translation_without_reenqueuing(tmp_path):
    from ttf_news.api import create_app
    store=Store(tmp_path/'api.db')
    analysis=store.create_analysis({'session':{'trade_date':'2026-10-19'}},'manual')
    store.finish_job(store.claim_job(),result=result())
    with TestClient(create_app(store,workers=False)) as client:
        url=f"/api/analyses/{analysis['id']}/translation"
        assert client.get(url).json()['status']=='not_requested'
        first=client.post(url).json()
        assert client.get(url).json()['id']==first['id']
        assert client.post(url).json()['id']==first['id']
        assert client.get(url+'?part=invalid').status_code==422
        assert client.get('/api/market').status_code==200


def quote_row():
    return {'marketId':6277389,'marketStrip':'Nov26','lastPrice':88.97,
            'lastTime':'10/19/2026 08:25 AM GMT','volume':30250}


def test_intraday_uses_utc_observation_times_and_rejects_future_or_wrong_contract():
    payload={'marketId':6277389,'bars':[['Mon Oct 19 08:20:00 2026',88.9],
             ['Mon Oct 19 08:20:00 2026',88.9],['Mon Oct 19 09:30:00 2026',90],
             ['Mon Oct 19 08:30:00 2026',float('nan')],['bad',80]]}
    points=parse_intraday(payload,'6277389','2026-10-19T08:40:00+00:00')
    assert points==[{'observed_at':'2026-10-19T08:20:00+00:00','value':88.9}]
    with pytest.raises(ValueError): parse_intraday(payload,'different','2026-10-19T08:40:00+00:00')
    q=parse_quote(quote_row(),'ICE.D.6277389','2026-10-19T08:40:00+00:00')
    assert q['observed_at']=='2026-10-19T08:25:00+00:00'
    assert q['minimum_delay_minutes']==15 and not q['verified_settlement']


def test_quote_first_observation_survives_repoll_without_leaking_back_in_time(tmp_path):
    store=Store(tmp_path/'quotes.db')
    q=parse_quote(quote_row(),'ICE.D.6277389','2026-10-19T08:40:00+00:00')
    points=[{'observed_at':'2026-10-19T08:20:00+00:00','value':88.9}]
    save_quote(store,q,points)
    save_quote(store,{**q,'available_at':'2026-10-19T08:45:00+00:00'},points)
    assert known_quote(store,q['contract'],'2026-10-19T08:39:59+00:00') is None
    assert known_quote(store,'ICE.D.other','2026-10-19T09:00:00+00:00') is None
    value=known_quote(store,q['contract'],'2026-10-19T08:42:00+00:00')
    assert value['available_at']=='2026-10-19T08:40:00+00:00'
    assert known_quote(store,q['contract'],'2026-10-19T10:00:00+00:00')['stale']
    with patch('ttf_news.context.price_context',return_value={'contract':q['contract'],'available':True}):
        assert market_view(store,'2026-10-19T08:39:59+00:00')['intraday']==[]
        chart=market_view(store,'2026-10-19T08:42:00+00:00')['intraday']
        assert chart[0]['value']==88.9 and 'open' not in chart[0]


@pytest.mark.asyncio
async def test_failed_quote_source_preserves_cached_observations(tmp_path):
    store=Store(tmp_path/'failed.db')
    q=parse_quote(quote_row(),'ICE.D.6277389','2026-10-19T08:40:00+00:00')
    save_quote(store,q,[])
    with patch('ttf_news.context.price_context',return_value={'contract':q['contract']}), \
         patch('httpx.AsyncClient.get',side_effect=httpx.ConnectError('source unavailable')):
        state=await refresh_market(store)
    assert state['status']=='failed'
    assert known_quote(store,q['contract'],'2026-10-19T09:00:00+00:00')['value']==88.97


def test_cfd_ohlc_requires_confirmed_contract_and_real_high_low():
    from ttf_news.candles import instrument_from_page, expected_symbol, parse_candles
    assert expected_symbol('Nov26')=='TRNLTTFMX2026'
    html=r'{\"SYS_NAME\":\"TRNLTTFMX2026\",\"IS_HIDDEN_API\":\"\",\"proxy_id\":\"22982245446866258\"}'
    assert instrument_from_page(html,'TRNLTTFMX2026')=='22982245446866258'
    with pytest.raises(ValueError): instrument_from_page(html,'TRNLTTFMZ2026')
    base={'timestamp':1792368000,'open':88.93,'high':89.28,'low':87.73,'close':89.12}
    bars=parse_candles({'history':[base,{**base,'high':80}, {**base,'timestamp':1800000000}]},'TRNLTTFMX2026','2026-10-19T09:00:00+00:00')
    assert len(bars)==1 and bars[0]['day']=='2026-10-19'
    assert bars[0]['price_type']=='cfd_reference_ohlc' and bars[0]['quote_side']=='ASK'
    assert not bars[0]['verified_settlement']
