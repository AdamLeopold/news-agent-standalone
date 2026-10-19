from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from ttf_news.store import Store
from ttf_news.evaluation import monitoring_report


def test_elapsed_seven_days_cannot_hide_stopped_service(tmp_path):
    db=Store(tmp_path/'monitor.sqlite3')
    now=datetime(2026,10,21,4,tzinfo=timezone.utc)
    start=now-timedelta(days=7,seconds=5)
    with patch('ttf_news.store.iso',return_value=start.isoformat()):
        db.metric('validation_started',{})
        db.metric('heartbeat',{})
    with patch('ttf_news.calendar.utcnow',return_value=now):
        report=monitoring_report(db)
    assert report['elapsed_days']>=7
    assert not report['seven_day_validation_complete']
    assert report['seconds_since_heartbeat']>180


def test_latest_baseline_excludes_development_failures(tmp_path):
    db=Store(tmp_path/'monitor.sqlite3')
    with patch('ttf_news.store.iso',return_value='2026-10-14T02:00:00+00:00'):
        db.metric('model_failure',{'error':'development test'})
    with patch('ttf_news.store.iso',return_value='2026-10-14T04:00:00+00:00'):
        db.metric('validation_started',{})
        db.metric('heartbeat',{})
        db.metric('model_success',{'kind':'short','seconds':12})
    with patch('ttf_news.calendar.utcnow',return_value=datetime(2026,10,14,4,1,tzinfo=timezone.utc)):
        report=monitoring_report(db)
    assert report['model_failures']==0
    assert report['model_successes']==1
    assert report['brief_latency']['median']==12
