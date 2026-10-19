"""Delayed ICE display quotes. First-observed timestamps protect analysis snapshots."""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import time

import httpx

from .calendar import iso, parse_time
from .prices import ICE_CONTRACTS_URL, ICE_DOCUMENTATION
from .store import dumps

INTRADAY_URL = 'https://www.ice.com/marketdata/api/productguide/charting/data/current-day'
DELAY_MINUTES = 15


def parse_intraday(payload, contract_id, available_at):
    if str(payload.get('marketId')) != str(contract_id):
        raise ValueError('ICE intraday contract does not match the requested contract')
    points = {}
    for bar in payload.get('bars', []):
        if not isinstance(bar, list) or len(bar) != 2:
            continue
        try:
            stamp = datetime.strptime(bar[0], '%a %b %d %H:%M:%S %Y').replace(tzinfo=timezone.utc)
            value = float(bar[1])
        except (ValueError, TypeError):
            continue
        if not math.isfinite(value) or value <= 0 or stamp > parse_time(available_at):
            continue
        points[iso(stamp)] = {'observed_at': iso(stamp), 'value': value}
    return sorted(points.values(), key=lambda p: p['observed_at'])


def parse_quote(row, contract, available_at):
    value = float(row['lastPrice'])
    stamp = datetime.strptime(row['lastTime'], '%m/%d/%Y %I:%M %p GMT').replace(tzinfo=timezone.utc)
    if not math.isfinite(value) or value <= 0 or stamp > parse_time(available_at):
        raise ValueError('Invalid ICE last-trade value or future timestamp')
    return {'contract': contract, 'contract_id': str(row['marketId']), 'contract_name': row['marketStrip'],
            'value': value, 'observed_at': iso(stamp), 'available_at': available_at,
            'unit': 'EUR/MWh', 'source': 'ICE public delayed market data', 'source_url': ICE_DOCUMENTATION,
            'price_type': 'delayed_last_trade', 'verified_settlement': False,
            'minimum_delay_minutes': DELAY_MINUTES, 'volume': row.get('volume')}


def save_quote(store, quote, points):
    # Stable identities retain the actual first time a quote/point became known locally.
    digest = hashlib.sha256(dumps([quote['contract'], quote['observed_at'], quote['value']]).encode()).hexdigest()
    with store.connection() as c:
        c.execute('INSERT OR IGNORE INTO market_quotes VALUES (?,?,?,?,?)',
                  (digest, quote['contract'], quote['observed_at'], quote['available_at'], dumps(quote)))
        for point in points:
            c.execute('INSERT OR IGNORE INTO market_points VALUES (?,?,?,?)',
                      (quote['contract'], point['observed_at'], quote['available_at'], point['value']))


def known_quote(store, contract, as_of):
    as_of = iso(parse_time(as_of))
    with store.connection() as c:
        row = c.execute('SELECT data FROM market_quotes WHERE contract=? AND available_at<=? AND observed_at<=? '
                        'ORDER BY observed_at DESC,available_at DESC LIMIT 1', (contract, as_of, as_of)).fetchone()
    if not row:
        return None
    data = json.loads(row[0])
    data['age_seconds'] = max(0, int((parse_time(as_of)-parse_time(data['observed_at'])).total_seconds()))
    data['stale'] = data['age_seconds'] > 3600
    return data


def market_view(store, as_of=None):
    from .context import price_context
    from .candles import candle_view
    as_of = as_of or iso()
    reference = price_context(store, as_of)
    contract = reference.get('contract')
    quote = known_quote(store, contract, as_of) if contract else None
    points = []
    if quote:
        # current-day endpoint can span the overnight session; retain the genuine timestamps.
        floor = iso(parse_time(quote['observed_at'])-timedelta(hours=30))
        with store.connection() as c:
            rows = c.execute('SELECT observed_at,value,MIN(available_at) AS available_at FROM market_points '
                             'WHERE contract=? AND available_at<=? AND observed_at BETWEEN ? AND ? '
                             'GROUP BY observed_at ORDER BY observed_at', (contract, as_of, floor, quote['observed_at'])).fetchall()
        points = [dict(row) for row in rows]
    states = {row['id']: row for row in store.sources()}
    return {'available': bool(quote), 'as_of': as_of, 'quote': quote, 'intraday': points,
            'candles': candle_view(store, reference.get('contract_name'), as_of),
            'reference': reference, 'source_state': states.get('ice_intraday', {}),
            'minimum_delay_minutes': DELAY_MINUTES, 'poll_seconds': 60,
            'note': 'ICE public data is delayed at least 15 minutes; chart points are sampled prices, not tick bars or official settlement.'}


async def refresh_market(store):
    from .context import price_context
    started = iso()
    previous = next((r for r in store.sources() if r['id']=='ice_intraday'), {})
    state = {**previous, 'source': 'ice_intraday', 'name': 'ICE intraday (delayed ≥15m)',
             'last_attempt_at': started}
    try:
        reference = price_context(store, started)
        contract = reference.get('contract')
        if not contract:
            raise ValueError('No identified front-month daily reference contract is available')
        contract_id = contract.rsplit('.', 1)[-1]
        async with httpx.AsyncClient(timeout=30, follow_redirects=True, trust_env=False) as client:
            response = await client.get(ICE_CONTRACTS_URL)
            response.raise_for_status()
            row = next((r for r in response.json() if str(r.get('marketId'))==contract_id), None)
            if not row:
                raise ValueError('Daily reference contract is absent from current ICE contract metadata')
            response = await client.get(INTRADAY_URL, params={'marketId': contract_id})
            response.raise_for_status()
            fetched = iso()
            points = parse_intraday(response.json(), contract_id, fetched)
            quote = parse_quote(row, contract, fetched)
        save_quote(store, quote, points)
        state.update(status='ok', last_success_at=fetched, last_error=None, contract=contract,
                     quote_time=quote['observed_at'], point_count=len(points), minimum_delay_minutes=DELAY_MINUTES)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        state.update(status='failed', last_error=(str(exc) or type(exc).__name__)[:600])
    store.set_source_state('ice_intraday', state)
    return state


async def market_worker(store):
    from .candles import refresh_candles
    next_candles = 0
    while True:
        started = time.monotonic()
        await refresh_market(store)
        if started >= next_candles:
            await refresh_candles(store)
            next_candles = time.monotonic()+600
        await asyncio.sleep(max(5, 60-(time.monotonic()-started)))
