"""Public Capital.com chart OHLC. Display reference only, never ICE labels."""
from datetime import datetime, timezone
import hashlib
import json
import math
import re
import time

import httpx

from .calendar import iso, parse_time
from .prices import contract_delivery_month
from .store import dumps

HISTORY_URL = 'https://api.backend-capital.com/proxy/trading/v1/quoteHistory'
PAGE_TEMPLATE = 'https://capital.com/de-de/markets/commodities/rohstoff-natural-gas-eu-ttf-{month}-{year}'
MONTH_CODES = 'FGHJKMNQUVXZ'


def expected_symbol(label):
    month = contract_delivery_month(label)
    if not month:
        raise ValueError('CFD candles require an identified delivery month')
    return f'TRNLTTFM{MONTH_CODES[month.month-1]}{month.year}'


def instrument_from_page(html, symbol):
    # Read only public product metadata. Never use account/session credentials.
    unescaped = html.replace('\\"', '"')
    match = re.search(r'"SYS_NAME"\s*:\s*"'+re.escape(symbol)+r'"(.{0,1600}?)"proxy_id"\s*:\s*"(\d+)"', unescaped)
    if not match:
        raise ValueError('Public CFD product page did not confirm the requested monthly instrument')
    return match[2]


def parse_candles(payload, symbol, available_at):
    rows = {}
    for raw in payload.get('history', []):
        try:
            stamp = datetime.fromtimestamp(float(raw['timestamp']), timezone.utc)
            values = {key: float(raw[key]) for key in ('open','high','low','close')}
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            continue
        if stamp > parse_time(available_at) or any(not math.isfinite(v) or v<=0 for v in values.values()):
            continue
        if values['high'] < max(values.values()) or values['low'] > min(values.values()):
            continue
        day=stamp.date().isoformat()
        rows[day] = {'day': day, 'period_start': iso(stamp), **values,
                     'symbol': symbol, 'source': 'Capital.com public chart', 'quote_side': 'ASK',
                     'unit': 'EUR/MWh', 'price_type': 'cfd_reference_ohlc', 'verified_settlement': False}
    if not rows:
        raise ValueError('Public CFD history returned no valid OHLC candles')
    return sorted(rows.values(),key=lambda r:r['day'])


def candle_view(store, label, as_of):
    try:
        symbol=expected_symbol(label)
    except (ValueError,TypeError):
        return {'available':False, 'items':[]}
    with store.connection() as c:
        rows = c.execute("SELECT data,available_at FROM market_candles WHERE source='capital' AND contract=? AND available_at<=? AND day<=? ORDER BY available_at",
                         (symbol,as_of,as_of[:10])).fetchall()
    candles={}
    for row in rows:
        data=json.loads(row['data'])
        data.update(available_at=row['available_at'], forming=data['day']==as_of[:10])
        candles[data['day']]=data
    state=next((r for r in store.sources() if r['id']=='capital_candles'),{})
    return {'available':bool(candles),'items':sorted(candles.values(),key=lambda r:r['day'])[-90:],
            'symbol':symbol,'contract_name':label,'source':'Capital.com public chart','quote_side':'ASK',
            'unit':'EUR/MWh','bar_timezone':'UTC','source_url':state.get('source_url'),
            'last_success_at':state.get('last_success_at'),'status':state.get('status','not_requested'),
            'poll_seconds':600,'price_type':'cfd_reference_ohlc','verified_settlement':False}


async def refresh_candles(store):
    from .context import price_context
    now=iso()
    previous=next((r for r in store.sources() if r['id']=='capital_candles'),{})
    state={**previous,'source':'capital_candles','name':'TTF CFD daily OHLC (Capital.com)', 'last_attempt_at':now}
    try:
        reference=price_context(store,now)
        label=reference.get('contract_name')
        symbol=expected_symbol(label)
        month=contract_delivery_month(label)
        page=PAGE_TEMPLATE.format(month=month.strftime('%b').lower(),year=month.year)
        async with httpx.AsyncClient(timeout=30,follow_redirects=True,trust_env=False) as client:
            instrument=state.get('instrument_id') if state.get('symbol')==symbol else None
            if not instrument:
                response=await client.get(page)
                response.raise_for_status()
                instrument=instrument_from_page(response.text,symbol)
            response=await client.post(HISTORY_URL,json={'depth':90,'instrumentId':instrument,'source':'ASK','timeframe':'D1','timestamp':int(time.time()*1000)},
                headers={'App-Version':'com.capital.website:1.1.8.web.03/17.15:57'})
            response.raise_for_status()
            fetched=iso()
            rows=parse_candles(response.json(),symbol,fetched)
        with store.connection() as c:
            for row in rows:
                digest=hashlib.sha256(dumps(row).encode()).hexdigest()
                c.execute('INSERT OR IGNORE INTO market_candles VALUES (?,?,?,?,?,?)',('capital',symbol,row['day'],digest,fetched,dumps(row)))
        state.update(status='ok',last_success_at=fetched,last_error=None,symbol=symbol,instrument_id=instrument,
                     source_url=page,contract_name=label,bar_count=len(rows),latest_day=rows[-1]['day'],
                     quote_side='ASK',bar_timezone='UTC',settlement_verified=False)
    except Exception as exc:
        state.update(status='failed',last_error=(str(exc) or type(exc).__name__)[:600])
    store.set_source_state('capital_candles',state)
    return state
