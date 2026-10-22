from datetime import datetime,timezone
from ttf_news.runtime import market_refresh_reason

NOW=datetime(2026,10,20,14,0,tzinfo=timezone.utc)
OLD={'available':True,'contract':'NOV26','latest_price':88.0}
NEW={'available':True,'contract':'NOV26','latest_price':88.5}


def test_failed_analysis_recovers_on_next_material_price_move():
    assert market_refresh_reason({'status':'failed','created_at':'2026-10-20T13:40:00Z'},OLD,NEW,NOW)=='market_price_change'


def test_pending_or_recent_failure_does_not_flood_live_queue():
    for status in ['running','queued']:
        assert market_refresh_reason({'status':status,'created_at':'2026-10-20T13:00:00Z'},OLD,NEW,NOW) is None
    assert market_refresh_reason({'status':'failed','created_at':'2026-10-20T13:59:00Z'},OLD,NEW,NOW) is None


def test_roll_detected_by_identity_not_by_cross_contract_price_difference():
    changed={**NEW,'contract':'DEC26','latest_price':88.0}
    assert market_refresh_reason({'status':'completed','created_at':'2026-10-20T13:40:00Z'},OLD,changed,NOW)=='contract_change'


def test_missing_or_insignificant_quote_does_not_create_forecast():
    latest={'status':'completed','created_at':'2026-10-20T13:40:00Z'}
    assert market_refresh_reason(latest,OLD,{'available':False},NOW) is None
    assert market_refresh_reason(latest,OLD,{**NEW,'latest_price':88.1},NOW) is None


def test_corrected_previous_reference_refreshes_even_when_quote_is_unchanged():
    latest={'status':'completed','created_at':'2026-10-20T13:40:00Z'}
    old={**OLD,'previous_date':'2026-10-19','previous_price':86.2,'extreme_threshold':4.0}
    # The unchanged 88 quote is now below the corrected 89.2 daily reference.
    corrected={**old,'previous_price':89.2}
    assert market_refresh_reason(latest,old,corrected,NOW)=='previous_reference_price_change'
    assert market_refresh_reason(latest,old,{**old,'previous_date':'2026-10-16'},NOW)=='previous_reference_date_change'


def test_threshold_change_refreshes_with_same_contract_and_price():
    latest={'status':'completed','created_at':'2026-10-20T13:40:00Z'}
    old={**OLD,'extreme_threshold':None}
    assert market_refresh_reason(latest,old,{**old,'extreme_threshold':4.0},NOW)=='extreme_threshold_change'
    assert market_refresh_reason(latest,{**old,'extreme_threshold':4.0},old,NOW)=='extreme_threshold_change'
    assert market_refresh_reason(latest,old,dict(old),NOW) is None


def test_reference_changes_still_respect_queue_and_retry_cooldown():
    old={**OLD,'previous_price':86.2}
    corrected={**old,'previous_price':89.2,'extreme_threshold':4.0}
    for status in ('queued','running'):
        assert market_refresh_reason({'status':status,'created_at':'2026-10-20T13:00:00Z'},old,corrected,NOW) is None
    assert market_refresh_reason({'status':'failed','created_at':'2026-10-20T13:59:00Z'},old,corrected,NOW) is None


def test_numeric_serialization_noise_does_not_refresh_but_exact_band_move_does():
    latest={'status':'completed','created_at':'2026-10-20T13:40:00Z'}
    old={**OLD,'previous_price':86.2,'extreme_threshold':4.0}
    same={**old,'previous_price':86.20000000001,'extreme_threshold':4.00000000001}
    assert market_refresh_reason(latest,old,same,NOW) is None
    assert market_refresh_reason(latest,old,{**old,'latest_price':88.3},NOW)=='market_price_change'


def test_roll_precedes_reference_comparison():
    latest={'status':'completed','created_at':'2026-10-20T13:40:00Z'}
    old={**OLD,'previous_price':86.2,'previous_date':'2026-10-19','extreme_threshold':4.0}
    rolled={**old,'contract':'DEC26','previous_price':100.0,'previous_date':'2026-10-16','extreme_threshold':8.0}
    assert market_refresh_reason(latest,old,rolled,NOW)=='contract_change'
