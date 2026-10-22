from copy import deepcopy
from ttf_news.context import market_baseline
from ttf_news.calendar import session_at
from ttf_news.schemas import CloseForecast
import pytest


ASOF='2026-10-20T12:10:00+00:00'
def reference():
    return {'contract':'ICE:NOV26','price_date':'2026-10-19','previous_price':88.0,
            'extreme_threshold':4.0,'intraday':{'contract':'ICE:NOV26','value':89.8,
            'observed_at':'2026-10-20T12:00:00+00:00','available_at':'2026-10-20T12:09:00+00:00',
            'price_type':'delayed_last_trade','stale':False}}


def test_known_same_contract_move_is_an_observation_not_settlement_forecast():
    baseline=market_baseline(reference(),session_at(ASOF),ASOF)
    assert baseline['available'] and baseline['observed_direction']=='up'
    assert baseline['observed_delta']==1.8 and not baseline['verified_settlement']
    assert baseline['kind']=='observed_reference_move'


@pytest.mark.parametrize('mutation', ['different_contract','future_quote','future_availability','old_day','stale','old_window'])
def test_wrong_identity_or_unavailable_quotes_never_make_baseline(mutation):
    r=reference()
    if mutation=='different_contract':r['intraday']['contract']='ICE:DEC26'
    elif mutation=='future_quote':r['intraday']['observed_at']='2026-10-20T13:00:00+00:00'
    elif mutation=='future_availability':r['intraday']['available_at']='2026-10-20T12:11:00+00:00'
    elif mutation=='old_day':r['price_date']='2026-10-16'
    elif mutation=='stale':r['intraday']['stale']=True
    else:r['intraday']['observed_at']='2026-10-19T14:00:00+00:00'
    assert not market_baseline(r,session_at(ASOF),ASOF)['available']


def test_null_forecast_has_explicit_status_and_never_flat_default():
    fields={'confidence':0.1,'summary':'缺乏当日价格与方向性新闻证据','basis':'无法形成有依据的收盘方向'}
    valid=CloseForecast(direction=None,evidence_status='insufficient_evidence',**fields)
    assert valid.direction is None
    with pytest.raises(ValueError):CloseForecast(direction='flat',evidence_status='insufficient_evidence',**fields)
    with pytest.raises(ValueError):CloseForecast(direction=None,evidence_status='forecast',**fields)
