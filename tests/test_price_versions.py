"""Daily references retain the time each economic observation became known."""
import json
import sqlite3
from datetime import date
from unittest.mock import patch

from ttf_news.context import price_context
from ttf_news.market import save_quote
from ttf_news.store import Store, dumps
from ttf_news.calendar import previous_day
from ttf_news.evaluation import enrich_outcomes


CONTRACT='ICE:DUTCH_TTF_GAS_FUTURES:D.6277389'
T1='2026-10-20T11:00:00+00:00'
T2='2026-10-20T13:00:00+00:00'


def observation(**changes):
    return {'contract':CONTRACT,'contract_label':'Nov26','delivery_month':'2026-11-01',
            'day':'2026-10-19','value':88.0,'price_type':'reference','verified':False,
            'unit':'EUR/MWh','fetched_at':T1,**changes}


def quote(db):
    save_quote(db,{'contract':CONTRACT,'value':89.8,'observed_at':'2026-10-20T12:00:00+00:00',
                   'available_at':'2026-10-20T12:09:00+00:00','price_type':'delayed_last_trade'},[])


def test_late_fetched_daily_reference_never_appears_in_earlier_as_of(tmp_path):
    db=Store(tmp_path/'late.db')
    db.upsert_price(observation(fetched_at=T2))
    quote(db)
    assert not price_context(db,'2026-10-20T12:10:00+00:00')['available']
    after=price_context(db,T2)
    assert after['available'] and after['previous_price']==88.0
    assert after['price_known_at']==T2


def test_later_price_revision_does_not_change_earlier_baseline_or_analysis(tmp_path):
    db=Store(tmp_path/'revised.db')
    db.upsert_price(observation())
    quote(db)
    asof='2026-10-20T12:10:00+00:00'
    before=price_context(db,asof)
    assert before['market_baseline']['observed_direction']=='up'
    record=db.create_analysis({'session':{'trade_date':'2026-10-20'},'price_reference':before},'live')
    frozen_input=db.analysis(record['id'],include_input=True)['input_snapshot']
    db.upsert_price(observation(value=91.0,fetched_at=T2))
    assert price_context(db,asof)==before
    later=price_context(db,T2)
    assert later['previous_price']==91.0
    assert later['market_baseline']['observed_direction']=='down'
    assert db.analysis(record['id'],include_input=True)['input_snapshot']==frozen_input
    with db.connection() as c:
        assert c.execute('SELECT COUNT(*) FROM price_versions').fetchone()[0]==2
        assert c.execute('SELECT value FROM prices').fetchone()[0]==91.0


def test_unchanged_repoll_keeps_first_known_clock_and_one_version(tmp_path):
    db=Store(tmp_path/'repoll.db')
    db.upsert_price(observation())
    db.upsert_price(observation(fetched_at=T2,provider='Refreshed description'))
    db=Store(db.path)  # A restart must not seed the new fetch clock as a revision.
    result=price_context(db,'2026-10-20T12:10:00+00:00')
    assert result['previous_price']==88.0 and result['price_known_at']==T1
    with db.connection() as c:
        versions=c.execute('SELECT known_at,data FROM price_versions').fetchall()
        assert len(versions)==1 and versions[0]['known_at']==T1
        assert json.loads(versions[0]['data'])['fetched_at']==T1


def test_price_identity_and_verification_revisions_are_known_only_later(tmp_path):
    db=Store(tmp_path/'identity.db')
    db.upsert_price(observation())
    db.upsert_price(observation(verified=True,fetched_at=T2))
    assert not price_context(db,'2026-10-20T12:00:00+00:00')['verified']
    assert price_context(db,T2)['verified']
    db.upsert_price(observation(contract='ICE:DEC26',delivery_month='2026-12-01',value=1e6,fetched_at=T1))
    assert price_context(db,'2026-10-20T12:00:00+00:00')['contract']==CONTRACT


def seed_legacy(path,item):
    raw=dumps(item)
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE prices (contract TEXT,day TEXT,price_type TEXT,value REAL,data TEXT NOT NULL, PRIMARY KEY(contract,day,price_type))')
        c.execute('INSERT INTO prices VALUES (?,?,?,?,?)',(item['contract'],item['day'],item['price_type'],item['value'],raw))
    return raw


def test_migration_preserves_original_row_and_uses_recorded_fetch_clock(tmp_path):
    path=tmp_path/'legacy.db'
    raw=seed_legacy(path,observation())
    db=Store(path)
    db=Store(path)
    with db.connection() as c:
        assert c.execute('SELECT data FROM prices').fetchone()[0]==raw
        versions=c.execute('SELECT known_at,data FROM price_versions').fetchall()
        assert len(versions)==1
        assert versions[0]['known_at']==T1 and versions[0]['data']==raw
    assert not price_context(db,'2026-10-20T10:00:00+00:00')['available']
    assert price_context(db,'2026-10-20T12:00:00+00:00')['available']


def test_legacy_without_local_clock_is_not_backdated_to_price_day(tmp_path):
    path=tmp_path/'unknown-clock.db'
    item=observation()
    item.pop('fetched_at')
    raw=seed_legacy(path,item)
    with patch('ttf_news.store.iso',return_value=T2):
        db=Store(path)
    assert not price_context(db,'2026-10-20T12:00:00+00:00')['available']
    assert price_context(db,T2)['price_known_at']==T2
    with db.connection() as c:
        assert c.execute('SELECT data FROM prices').fetchone()[0]==raw


def test_new_reference_without_observation_clock_is_known_at_insertion(tmp_path):
    db=Store(tmp_path/'insert-clock.db')
    item=observation()
    item.pop('fetched_at')
    with patch('ttf_news.store.iso',return_value=T2):
        db.upsert_price(item)
    assert not price_context(db,'2026-10-20T12:00:00+00:00')['available']
    assert price_context(db,T2)['price_known_at']==T2


def test_same_fetch_clock_revisions_are_appended_without_overwriting(tmp_path):
    db=Store(tmp_path/'same-clock.db')
    db.upsert_price(observation())
    db.upsert_price(observation(value=91.0))
    assert price_context(db,T1)['previous_price']==88.0
    assert price_context(db,'2026-10-20T11:00:00.000001+00:00')['previous_price']==91.0
    with db.connection() as c:
        assert c.execute('SELECT COUNT(*) FROM price_versions').fetchone()[0]==2


def test_later_history_revision_does_not_recompute_earlier_volatility(tmp_path):
    db=Store(tmp_path/'volatility.db')
    day=date(2026,10,20)
    history=[]
    for index in range(61):
        day=previous_day(day)
        item=observation(day=day.isoformat(),value=88+index%3)
        history.append(item)
        db.upsert_price(item)
    before=price_context(db,'2026-10-20T12:10:00+00:00')
    assert before['sigma60'] is not None
    db.upsert_price({**history[20],'value':1000.0,'fetched_at':T2})
    assert price_context(db,'2026-10-20T12:10:00+00:00')['sigma60']==before['sigma60']
    assert price_context(db,T2)['extreme_threshold']>before['extreme_threshold']


def test_retrospective_contract_selection_uses_now_known_prices_without_intraday(tmp_path):
    db=Store(tmp_path/'retrospective.db')
    db.upsert_price(observation(day='2026-10-08'))
    quote(db)
    assert not price_context(db,'2026-10-09T12:00:00+00:00')['available']
    result=price_context(db,'2026-10-20T12:10:00+00:00',reference_trade_date='2026-10-09')
    assert result['contract']==CONTRACT and result['price_date']=='2026-10-08'
    assert result['price_known_at']==T1 and result['intraday'] is None
    assert not result['market_baseline']['available']
    assert result['market_baseline']['reason']=='retrospective_contract_selection'


def test_backfill_can_enrich_outcome_now_without_inventing_past_availability(tmp_path):
    db=Store(tmp_path/'backfill-outcome.db')
    now='2026-10-14T03:00:00+00:00'
    db.upsert_event({'id':'outage','source':'test','title':'Norway outage','event_date':'2026-10-09'})
    for day,value in (('2026-10-08',40.0),('2026-10-09',41.0),('2026-10-12',42.0),('2026-10-14',9000.0)):
        db.upsert_price(observation(day=day,value=value,fetched_at=now))
    # A later revision is in the latest table, but is not available at this run.
    db.upsert_price(observation(day='2026-10-12',value=1000.0,fetched_at='2026-10-15T03:00:00+00:00'))
    assert not price_context(db,'2026-10-09T12:00:00+00:00')['available']
    with patch('ttf_news.evaluation.iso',return_value=now):
        enrich_outcomes(db)
    event=db.events()[0]
    outcome=event['outcomes']['1d']
    assert outcome['contract']==CONTRACT and outcome['change']==1.0
    assert outcome['end_date']=='2026-10-12' and outcome['available_at']==now
    assert event['outcome_available_at']==now and '3d' not in event['outcomes']
