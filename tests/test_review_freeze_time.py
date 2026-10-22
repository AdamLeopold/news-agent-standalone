from unittest.mock import patch

import pytest

from ttf_news.calendar import parse_time
from ttf_news.runtime import freeze_due
from test_temporal_integration import finish_analysis, store


@pytest.mark.parametrize('review_time,exclude_latest', [
    ('2026-10-14T15:14:59.999999+00:00', True),
    ('2026-10-14T15:15:00+00:00', True),
    ('2026-10-14T15:15:00.000001+00:00', False),
])
def test_freeze_uses_only_review_known_at_settlement(tmp_path, review_time, exclude_latest):
    db = store(tmp_path)
    earlier = finish_analysis(db, created='2026-10-14T02:00:00+00:00', completed='2026-10-14T14:00:00+00:00')
    latest = finish_analysis(db, created='2026-10-14T03:00:00+00:00', completed='2026-10-14T15:10:00+00:00')
    original = db.analysis_protected_hashes(latest)
    review = {'review_id': 'test-time-review', 'status': 'invalid', 'affected_parts': ['brief'],
              'reason_code': 'price_anchor', 'message_zh': '价格基准复核未通过',
              'message_en': 'Price baseline review failed', 'reviewer': 'isolated_test'}
    with patch('ttf_news.store.iso', return_value=review_time):
        db.append_analysis_review(latest, review, expected_hashes=original)
    freeze_due(db, parse_time('2026-10-14T15:17:00+00:00'))
    with db.connection() as connection:
        chosen = connection.execute("SELECT analysis_id FROM daily_snapshots WHERE trade_date='2026-10-14'").fetchone()[0]
    assert chosen == (earlier if exclude_latest else latest)
    assert db.analysis_protected_hashes(latest) == original
    # A later clearance and another maintenance pass preserve the frozen row.
    with patch('ttf_news.store.iso', return_value='2026-10-15T01:00:00+00:00'):
        db.append_analysis_review(latest, {**review, 'review_id': 'later-clearance', 'status': 'cleared'}, expected_hashes=original)
    freeze_due(db, parse_time('2026-10-15T01:00:00+00:00'))
    with db.connection() as connection:
        assert connection.execute("SELECT analysis_id FROM daily_snapshots WHERE trade_date='2026-10-14'").fetchone()[0] == chosen


def test_delayed_older_job_does_not_replace_newer_completed_forecast(tmp_path):
    db = store(tmp_path)
    older = finish_analysis(db, created='2026-10-14T02:00:00+00:00', completed='2026-10-14T15:14:00+00:00')
    newer = finish_analysis(db, created='2026-10-14T03:00:00+00:00', completed='2026-10-14T15:10:00+00:00')
    assert db.latest_analysis(trade_date='2026-10-14', completed_only=True, live_only=True)['id'] == newer
    freeze_due(db, parse_time('2026-10-14T15:17:00+00:00'))
    with db.connection() as connection:
        assert connection.execute("SELECT analysis_id FROM daily_snapshots WHERE trade_date='2026-10-14'").fetchone()[0] == newer
    assert db.analysis(older)['frozen'] == 0
