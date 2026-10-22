"""Cached decision renderings should not wait behind model inference backlog."""
import pytest

from ttf_news.store import Store


@pytest.mark.parametrize('structured', [False, True])
def test_new_render_jobs_precede_inference_only_for_shared_structured_plans(tmp_path, structured):
    store = Store(tmp_path / 'news.sqlite3')
    context = {'session': {'trade_date': '2026-10-23'}, 'as_of': '2026-10-22T16:00:00Z'}
    original = store.create_analysis(context, kind='manual', priority=0)
    first = store.claim_job()
    store.finish_job(first, result={'news_impact': {'direction': 'flat'}, '_meta': {
        'structured': {'catalog': {}, 'plan': {}}} if structured else {}})
    backlog = store.create_analysis(context, kind='live', priority=10)
    store.request_detail(original['id'])
    translated = store.request_translation(original['id'])
    with store.connection() as connection:
        jobs = {row['kind']: dict(row) for row in connection.execute(
            "SELECT * FROM jobs WHERE status='queued'")}
    assert jobs['short']['priority'] == 10
    assert jobs['detail']['priority'] == (1 if structured else 15)
    assert jobs['translation']['priority'] == (1 if structured else 30)
    selected = store.claim_job()
    if structured:
        assert selected['analysis_id'] in (original['id'], translated['id'])
        assert selected['kind'] in ('detail', 'translation')
        assert store.analysis(backlog['id'])['status'] == 'queued'
    else:
        assert selected['analysis_id'] == backlog['id']
