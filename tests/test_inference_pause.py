from unittest.mock import AsyncMock,patch

from fastapi.testclient import TestClient

from ttf_news.api import create_app
from ttf_news.store import Store


class Analyst:
    async def health(self):
        return {'available':True,'model':'test'}


def test_inference_pause_keeps_market_collection_maintenance_and_queue(tmp_path,monkeypatch):
    monkeypatch.setenv('TTF_NEWS_INFER','0');monkeypatch.setenv('TTF_NEWS_COLLECT','1')
    store=Store(tmp_path/'pause.sqlite3')
    queued=store.create_analysis({'session':{'trade_date':'2026-10-21'}},'manual')
    workers={name:AsyncMock() for name in ('inference_worker','collection_worker','maintenance_worker')}
    with patch('ttf_news.api.import_legacy',lambda _:None),patch('ttf_news.runtime.inference_worker',workers['inference_worker']),patch('ttf_news.runtime.collection_worker',workers['collection_worker']),patch('ttf_news.runtime.maintenance_worker',workers['maintenance_worker']),patch('ttf_news.market.market_worker',new_callable=AsyncMock) as market:
        with TestClient(create_app(store=store,analyst=Analyst(),workers=True)) as client:
            health=client.get('/api/health').json()
            assert health['inference']=={'enabled':False}
            assert health['collector']['enabled'] is True
            assert health['model']['available'] is True
        workers['inference_worker'].assert_not_called()
        assert workers['collection_worker'].await_count==1
        assert workers['maintenance_worker'].await_count==1
        assert market.await_count==1
    assert store.analysis(queued['id'])['status']=='queued'
    with store.connection() as c:
        assert c.execute('SELECT status FROM jobs WHERE analysis_id=?',(queued['id'],)).fetchone()[0]=='queued'
        assert c.execute("SELECT COUNT(*) FROM metrics WHERE type='heartbeat'").fetchone()[0]>=1


def test_default_inference_path_and_workers_false_health(tmp_path,monkeypatch):
    monkeypatch.delenv('TTF_NEWS_INFER',raising=False);monkeypatch.setenv('TTF_NEWS_COLLECT','0')
    store=Store(tmp_path/'default.sqlite3')
    with patch('ttf_news.api.import_legacy',lambda _:None),patch('ttf_news.runtime.inference_worker',new_callable=AsyncMock) as inference,patch('ttf_news.market.market_worker',new_callable=AsyncMock):
        with TestClient(create_app(store=store,analyst=Analyst(),workers=True)) as client:
            assert client.get('/api/health').json()['inference']=={'enabled':True}
        assert inference.await_count==1
        with TestClient(create_app(store=store,analyst=Analyst(),workers=False)) as client:
            assert client.get('/api/health').json()['inference']=={'enabled':False}
        assert inference.await_count==1
