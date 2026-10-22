import hashlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from ttf_news.api import create_app
from ttf_news.store import Store,dumps


def complete(store, identifier, *, created='2026-10-20T12:00:00+00:00', kind='live', news_id=None):
    context={'as_of':created,'session':{'trade_date':'2026-10-21'},'current_news':{'id':'N0'}}
    result={'news_impact':{'direction':'flat'},'close_forecast':{'direction':'down'},
            '_meta':{'prompt_version':'ttf-news-standalone-2.0','as_of':created}}
    with store.connection() as c:
        c.execute("INSERT INTO analyses(id,news_id,kind,status,created_at,completed_at,trade_date,input_snapshot,result) VALUES (?,?,?,'completed',?,?,?, ?,?)",
                  (identifier,news_id,kind,created,created,'2026-10-21',dumps(context),dumps(result)))
    return result


def review(identifier='review-one', **changes):
    return {'review_id':identifier,'status':'invalid','reason_code':'price_baseline_review_failed',
            'affected_parts':['brief'],'reviewer':'source_time_review',
            'message_zh':'目标交易日缺前一交易日同合约日价，原收盘判断使用了错误价格基准。',
            'message_en':'The previous trading day reference is missing; the original closing outlook used the wrong price baseline.',**changes}


def raw_protected(store):
    with store.connection() as c:
        return ([tuple(r) for r in c.execute('SELECT id,input_snapshot,result,detail_result FROM analyses ORDER BY id')],
                [tuple(r) for r in c.execute('SELECT * FROM daily_snapshots ORDER BY trade_date')],
                [tuple(r) for r in c.execute("SELECT * FROM metrics WHERE type='validation_started' ORDER BY id")])


def test_review_append_preserves_all_canonical_bytes_frozen_snapshot_and_baseline(tmp_path):
    store=Store(tmp_path/'review.sqlite3');complete(store,'old');complete(store,'new')
    with store.connection() as c:
        c.execute("INSERT INTO daily_snapshots VALUES ('2026-10-20','cutoff','old','frozen')")
        c.execute("INSERT INTO metrics(created_at,type,data) VALUES ('original','validation_started','{\"v\":1}')")
    before=raw_protected(store);hashes=store.analysis_protected_hashes('new')
    first=store.append_analysis_review('new',review(),expected_hashes=hashes)
    assert first['status']=='invalid' and first['invalid_parts']==['brief']
    assert store.analysis('new')['result']['close_forecast']['direction']=='down'
    assert raw_protected(store)==before
    with store.connection() as c:count=c.execute("SELECT COUNT(*) FROM metrics WHERE type='analysis_review'").fetchone()[0]
    store.append_analysis_review('new',review(),expected_hashes=hashes)
    with store.connection() as c:assert c.execute("SELECT COUNT(*) FROM metrics WHERE type='analysis_review'").fetchone()[0]==count
    assert raw_protected(store)==before


def test_new_detail_is_not_covered_by_brief_review_and_cannot_clear_it(tmp_path):
    store=Store(tmp_path/'review.sqlite3');complete(store,'new');old=store.analysis_protected_hashes('new')
    # A newly completed detail is independent from the earlier reviewed brief.
    with store.connection() as c:c.execute('UPDATE analyses SET detail_result=?,detail_status=? WHERE id=?',(dumps({'detail':{'summary':'new'}}),'completed','new'))
    store.append_analysis_review('new',review(),expected_hashes=old)
    assert store.analysis_review('new')['invalid_parts']==['brief']
    current=store.analysis_protected_hashes('new')
    store.append_analysis_review('new',review('detail-valid',status='cleared',affected_parts=['detail']),expected_hashes=current)
    state=store.analysis_review('new')
    assert state['invalid_parts']==['brief'] and state['parts']['detail']['status']=='cleared'
    assert state['parts']['brief']['status']=='invalid'


def test_review_hash_mismatch_and_missing_detail_cannot_invalidate_other_version(tmp_path):
    store=Store(tmp_path/'review.sqlite3');complete(store,'new');hashes=store.analysis_protected_hashes('new')
    wrong={**hashes,'result':'a'*64}
    with pytest.raises(ValueError,match='changed'):store.append_analysis_review('new',review(),expected_hashes=wrong)
    with pytest.raises(ValueError,match='missing result'):store.append_analysis_review('new',review(affected_parts=['detail']),expected_hashes=hashes)
    assert store.analysis_review('new') is None


def test_only_invalid_record_is_not_selected_and_detail_hash_cannot_cover_later_detail(tmp_path):
    store=Store(tmp_path/'review.sqlite3');complete(store,'new')
    with store.connection() as c:c.execute('UPDATE analyses SET detail_result=? WHERE id=?',('{"detail":"first"}','new'))
    hashes=store.analysis_protected_hashes('new')
    store.append_analysis_review('new',review(),expected_hashes=hashes)
    store.append_analysis_review('new',review('detail-one',affected_parts=['detail']),expected_hashes=hashes)
    assert store.analysis_review('new')['invalid_parts']==['detail','brief']
    with store.connection() as c:c.execute('UPDATE analyses SET detail_result=? WHERE id=?',('{"detail":"later"}','new'))
    assert store.analysis_review('new')['invalid_parts']==['brief']
    assert store.latest_analysis(completed_only=True,exclude_invalid=True) is None


def test_newest_independent_part_review_controls_only_that_part(tmp_path):
    store=Store(tmp_path/'review.sqlite3');complete(store,'new');hashes=store.analysis_protected_hashes('new')
    store.append_analysis_review('new',review(),expected_hashes=hashes)
    store.append_analysis_review('new',review('source-fixed',status='cleared'),expected_hashes=hashes)
    assert store.analysis_review('new')['status']=='cleared'
    with store.connection() as c:assert c.execute("SELECT COUNT(*) FROM metrics WHERE type='analysis_review'").fetchone()[0]==2


def test_review_as_of_keeps_exact_boundary_and_never_uses_later_clearance(tmp_path):
    store=Store(tmp_path/'review.sqlite3');complete(store,'new');hashes=store.analysis_protected_hashes('new')
    with patch('ttf_news.store.iso',return_value='2026-10-20T15:15:00.000001+00:00'):
        store.append_analysis_review('new',review(),expected_hashes=hashes)
    assert store.analysis_review('new',as_of='2026-10-20T15:15:00.000000+00:00') is None
    assert store.analysis_review('new',as_of='2026-10-20T15:15:00.000001+00:00')['status']=='invalid'
    assert store.analysis_review('new',as_of='2026-10-20T17:15:00.000001+02:00')['status']=='invalid'
    with patch('ttf_news.store.iso',return_value='2026-10-20T15:16:00+00:00'):
        store.append_analysis_review('new',review('later-clear',status='cleared'),expected_hashes=hashes)
    assert store.analysis_review('new')['status']=='cleared'
    assert store.analysis_review('new',as_of='2026-10-20T15:15:30+00:00')['status']=='invalid'
    assert store.analysis_review('new',as_of='2026-10-20T15:16:00+00:00')['status']=='cleared'


def test_latest_dashboard_excludes_invalid_but_article_and_detail_keep_original(tmp_path):
    store=Store(tmp_path/'review.sqlite3');complete(store,'older',created='2026-10-20T18:00:00+00:00')
    article=store.upsert_news({'source':'test','url':'https://example.com/gas','title':'Gas notice','summary':'No immediate flow facts',
                               'published_at':'2026-10-20T18:30:00+00:00','first_seen_at':'2026-10-20T18:31:00+00:00','timestamp_precision':'exact'})
    original=complete(store,'invalid',created='2026-10-20T19:00:00+00:00',news_id=article['id'])
    store.append_analysis_review('invalid',review(),expected_hashes=store.analysis_protected_hashes('invalid'))
    assert store.latest_analysis(completed_only=True,live_only=True)['id']=='invalid'
    assert store.latest_analysis(completed_only=True,live_only=True,exclude_invalid=True)['id']=='older'
    with patch('ttf_news.api.import_legacy',lambda _:None),patch('ttf_news.api.iso',return_value='2026-10-20T20:00:00+00:00'):
        with TestClient(create_app(store=store,analyst=object(),workers=False)) as client:
            dashboard=client.get('/api/dashboard').json()
            assert dashboard['latest_analysis']['id']=='older'
            assert dashboard['excluded_latest_analysis']['id']=='invalid'
            assert dashboard['excluded_latest_analysis']['review']['invalid_parts']==['brief']
            record=client.get('/api/analyses/invalid?include_input=true').json()
            assert record['result']==original and record['review']['status']=='invalid'
            assert record['input_snapshot']['session']['trade_date']=='2026-10-21'
            card=client.get('/api/news/'+article['id']).json()
            assert card['analysis']==original and card['review']['status']=='invalid'


def test_review_apply_preflight_is_readonly_and_does_not_require_detail_stasis(tmp_path):
    script=Path(__file__).resolve().parents[1]/'scripts/apply-analysis-reviews.py'
    spec=importlib.util.spec_from_file_location('review_cli',script);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    store=Store(tmp_path/'review.sqlite3');complete(store,'new');hashes=store.analysis_protected_hashes('new')
    with store.connection() as c:c.execute('UPDATE analyses SET detail_result=? WHERE id=?',('{"detail":"later"}','new'))
    manifest={'pending_reviews':[{'analysis_id':'new','protected_hashes':hashes,'review':review()}]}
    before=store.path.read_bytes();result=module.preflight(store.path,manifest)
    assert result[0]['protected_hashes_match'] is True and store.path.read_bytes()==before
    assert store.analysis_review('new') is None
    manifest['pending_reviews'][0]['protected_hashes']['result']='f'*64
    with pytest.raises(ValueError,match='changed'):module.preflight(store.path,manifest)
