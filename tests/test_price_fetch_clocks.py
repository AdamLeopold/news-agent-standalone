"""Daily response data becomes locally known when its response is available."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from ttf_news.context import price_context
from ttf_news.prices import refresh_prices, refresh_ice_chart_references
from ttf_news.store import Store


def clock(*times):
    values=iter(datetime.fromisoformat(value) for value in times)
    class ResponseClock(datetime):
        @classmethod
        def now(cls,tz=None):
            return next(values).astimezone(tz or timezone.utc)
    return ResponseClock


def response(payload):
    return httpx.Response(200,json=payload,request=httpx.Request('GET','https://example.test/prices'))


@pytest.mark.asyncio
async def test_dbnomics_fetch_clock_is_after_http_response_not_request_start(tmp_path):
    db=Store(tmp_path/'dbnomics.db')
    payload={'dataset':{'dimensions_values_labels':{'CONTRACT':{'6277389':'Nov26'}}},
             'series':{'num_found':1,'docs':[{'dimensions':{'CONTRACT':'6277389','FREQ':'D'},
                                              'period':['2026-10-19'],'value':[88.0]}]}}
    with patch('ttf_news.prices.datetime',clock('2026-10-20T10:00:00+00:00',
                                               '2026-10-20T10:00:10+00:00',
                                               '2026-10-20T10:00:11+00:00')), \
         patch('httpx.AsyncClient.get',new=AsyncMock(return_value=response(payload))), \
         patch('ttf_news.prices.refresh_ice_chart_references',new=AsyncMock(return_value={'status':'ok'})):
        state=await refresh_prices(db)
    assert state['status']=='ok' and state['last_attempt_at']=='2026-10-20T10:00:00+00:00'
    assert not price_context(db,'2026-10-20T10:00:09+00:00')['available']
    assert price_context(db,'2026-10-20T10:00:10+00:00')['price_known_at']=='2026-10-20T10:00:10+00:00'


@pytest.mark.asyncio
async def test_ice_chart_fetch_clock_is_after_chart_response(tmp_path):
    db=Store(tmp_path/'ice.db')
    replies=[response([{'marketId':6277389,'marketStrip':'Nov26'}]),
             response({'marketId':6277389,'bars':[['Mon Oct 19 00:00:00 2026',88.0]]})]
    with patch('ttf_news.prices.datetime',clock('2026-10-20T10:00:00+00:00',
                                               '2026-10-20T10:00:01+00:00',
                                               '2026-10-20T10:00:20+00:00',
                                               '2026-10-20T10:00:21+00:00')), \
         patch('httpx.AsyncClient.get',new=AsyncMock(side_effect=replies)), \
         patch('ttf_news.prices.asyncio.sleep',new=AsyncMock()):
        state=await refresh_ice_chart_references(db)
    assert state['status']=='ok' and state['last_attempt_at']=='2026-10-20T10:00:00+00:00'
    assert not price_context(db,'2026-10-20T10:00:19+00:00')['available']
    assert price_context(db,'2026-10-20T10:00:20+00:00')['price_known_at']=='2026-10-20T10:00:20+00:00'
