"""Fault injection uses disposable SQLite files and never calls a model."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ttf_news.runtime import inference_worker
from ttf_news.store import Store


CONTEXT = {'session': {'trade_date': '2026-10-20'}, 'current_news': {'id': 'original-news'}}
BRIEF = {'close_forecast': {'direction': 'up'}, '_meta': {'prompt_version': 'fixture-original'}}
OUTPUT = {'close_forecast': {'direction': 'down'}, '_meta': {'prompt_version': 'fixture-new'}}


def prepare_job(tmp_path, kind, *, live=False):
    store = Store(tmp_path / 'isolated.sqlite3')
    analysis = store.create_analysis(deepcopy(CONTEXT), 'live' if live else 'manual')
    translation = None
    if kind != 'short':
        store.finish_job(store.claim_job(), result=deepcopy(BRIEF))
        with store.connection() as c:
            c.execute('UPDATE analyses SET frozen=1 WHERE id=?', (analysis['id'],))
        if kind == 'detail':
            store.request_detail(analysis['id'])
        else:
            translation = store.request_translation(analysis['id'])
    before = store.analysis(analysis['id'], include_input=True)
    return store, analysis['id'], translation, before


def run_one(store, analyst, monkeypatch):
    """Run exactly one existing job, then cancel at the next claim boundary."""
    claim = store.claim_job
    attempted = False

    def once():
        nonlocal attempted
        if attempted:
            raise asyncio.CancelledError
        attempted = True
        job = claim()
        assert job is not None
        return job

    with monkeypatch.context() as patch:
        patch.setattr(store, 'claim_job', once)
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(inference_worker(store, analyst))


def output_record(store, identifier, kind, translation):
    if kind == 'translation':
        return store.translation_record(translation['id'])
    record = store.analysis(identifier)
    if kind == 'detail':
        return {'status': record['detail_status'], 'result': record['detail_result'],
                'error': record['detail_error']}
    return record


def assert_original_preserved(store, identifier, kind, before):
    current = store.analysis(identifier, include_input=True)
    assert current['input_snapshot'] == before['input_snapshot']
    if kind == 'translation':
        assert current == before
    elif kind == 'detail':
        for field in ('status', 'result', 'completed_at', 'error', 'frozen'):
            assert current[field] == before[field]


def mock_generation(monkeypatch, *, error=None):
    generate = AsyncMock(side_effect=error) if error else AsyncMock(return_value=deepcopy(OUTPUT))
    monkeypatch.setattr('ttf_news.translation.translate_analysis', generate)
    return SimpleNamespace(analyze=generate, reasoning='medium')


@pytest.mark.parametrize('kind', ['short', 'detail', 'translation'])
def test_success_metric_failure_preserves_committed_result(tmp_path, monkeypatch, kind):
    store, identifier, translation, before = prepare_job(tmp_path, kind)
    calls = []

    def broken_metric(name, data):
        calls.append(name)
        raise OSError('injected telemetry write failure')

    monkeypatch.setattr(store, 'metric', broken_metric)
    run_one(store, mock_generation(monkeypatch), monkeypatch)
    completed = output_record(store, identifier, kind, translation)
    assert completed['status'] == 'completed'
    assert completed['result'] == OUTPUT
    assert completed['error'] is None
    assert calls == ['translation_success' if kind == 'translation' else 'model_success']
    assert_original_preserved(store, identifier, kind, before)


@pytest.mark.parametrize('kind', ['short', 'detail', 'translation'])
def test_post_commit_sse_failure_is_not_a_failed_generation(tmp_path, monkeypatch, kind):
    store, identifier, translation, before = prepare_job(tmp_path, kind)
    emit = store.emit

    def broken_emit(name, data):
        if name in ('analysis_completed', 'translation_completed'):
            raise OSError('injected post-commit SSE failure')
        return emit(name, data)

    monkeypatch.setattr(store, 'emit', broken_emit)
    run_one(store, mock_generation(monkeypatch), monkeypatch)
    completed = output_record(store, identifier, kind, translation)
    assert completed['status'] == 'completed'
    assert completed['result'] == OUTPUT
    assert completed['error'] is None
    with store.connection() as c:
        kinds = [r[0] for r in c.execute('SELECT type FROM metrics')]
        assert c.execute("SELECT status FROM jobs ORDER BY rowid DESC LIMIT 1").fetchone()[0] == 'completed'
    assert kinds == ['post_commit_notification_failure',
                     'translation_success' if kind == 'translation' else 'model_success']
    assert_original_preserved(store, identifier, kind, before)


def test_live_translation_enqueue_and_failure_metric_do_not_erase_brief(tmp_path, monkeypatch):
    store, identifier, translation, before = prepare_job(tmp_path, 'short', live=True)
    metric = store.metric

    def broken_enqueue(*args, **kwargs):
        raise OSError('injected translation enqueue failure')

    def partial_metric(name, data):
        if name == 'translation_enqueue_failure':
            raise OSError('injected enqueue telemetry failure')
        metric(name, data)

    monkeypatch.setattr(store, 'request_translation', broken_enqueue)
    monkeypatch.setattr(store, 'metric', partial_metric)
    run_one(store, mock_generation(monkeypatch), monkeypatch)
    record = store.analysis(identifier, include_input=True)
    assert record['status'] == 'completed' and record['result'] == OUTPUT
    assert record['error'] is None and record['input_snapshot'] == CONTEXT
    with store.connection() as c:
        assert [r[0] for r in c.execute('SELECT type FROM metrics')] == ['model_success']
    # A temporary enqueue failure can be retried from the preserved source.
    monkeypatch.undo()
    assert store.request_translation(identifier)['status'] == 'queued'


@pytest.mark.parametrize('kind', ['short', 'detail', 'translation'])
@pytest.mark.parametrize('defect', ['generation', 'commit'])
def test_real_generation_or_precommit_failure_remains_failed_and_retryable(tmp_path, monkeypatch, kind, defect):
    store, identifier, translation, before = prepare_job(tmp_path, kind)
    analyst = mock_generation(monkeypatch, error=ValueError('injected generation failure') if defect == 'generation' else None)
    finish = store.finish_job
    if defect == 'commit':
        def broken_commit(job, result=None, error=None):
            if result is not None:
                raise OSError('injected precommit failure')
            return finish(job, result=result, error=error)
        monkeypatch.setattr(store, 'finish_job', broken_commit)
    run_one(store, analyst, monkeypatch)
    failed = output_record(store, identifier, kind, translation)
    assert failed['status'] == 'failed'
    assert failed['result'] is None
    assert ('injected generation failure' if defect == 'generation' else 'injected precommit failure') in failed['error']
    assert_original_preserved(store, identifier, kind, before)
    with store.connection() as c:
        assert [r[0] for r in c.execute('SELECT type FROM metrics')] == [
            'translation_failure' if kind == 'translation' else 'model_failure']
    if kind == 'detail':
        assert store.request_detail(identifier)['status'] == 'queued'
    elif kind == 'translation':
        assert store.request_translation(identifier, retry=True)['status'] == 'queued'
    else:
        retry = store.create_analysis(store.analysis(identifier, include_input=True)['input_snapshot'], 'manual')
        assert retry['status'] == 'queued' and retry['id'] != identifier
        assert store.analysis(identifier)['status'] == 'failed'


@pytest.mark.parametrize('kind', ['short', 'detail', 'translation'])
def test_interrupted_job_recovery_retains_original_input_and_completed_source(tmp_path, monkeypatch, kind):
    store, identifier, translation, before = prepare_job(tmp_path, kind)
    job = store.claim_job()
    assert job['kind'] == kind
    store.recover_jobs()
    queued = output_record(store, identifier, kind, translation)
    assert queued['status'] == 'queued'
    assert_original_preserved(store, identifier, kind, before)
    run_one(store, mock_generation(monkeypatch), monkeypatch)
    assert output_record(store, identifier, kind, translation)['result'] == OUTPUT
