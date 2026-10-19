import csv
import io
import json
from .store import dumps
from .calendar import iso
from .context import aggregate


def render_export(store,filename):
    if filename in ("news.csv","news.jsonl"):
        rows = store.news(1000000)
    elif filename in ("events.jsonl","events.csv"):
        rows = store.events(1000000)
    elif filename in ("daily.csv","daily.jsonl"):
        from datetime import date
        from .calendar import next_day, session_at
        from .config import BACKFILL_START
        with store.connection() as c:
            first = c.execute("SELECT MIN(trade_date) FROM news WHERE trade_date IS NOT NULL").fetchone()[0]
        day = next_day(date.fromisoformat(min(first or BACKFILL_START,BACKFILL_START)),include=True)
        last = date.fromisoformat(session_at()['trade_date'])
        days = []
        while day<=last:
            days.append(day.isoformat())
            day=next_day(day)
        from .calendar import session_for_day
        rows = [{"trade_date":day,"data_quality":"article_level","view":"retrospective_backfilled","coverage_note":"零条仅表示当前数据集无可归属精确时间记录，结合来源健康和覆盖报告判断。","session":session_for_day(day),**aggregate(store.window_news(day,iso(),historical=True))} for day in days]
    elif filename=="legacy_daily.jsonl":
        with store.connection() as c:
            rows = [json.loads(r[0]) for r in c.execute("SELECT data FROM legacy_daily ORDER BY trade_date")]
    elif filename in ('prices.csv','prices.jsonl'):
        with store.connection() as c:
            rows = [json.loads(r[0]) for r in c.execute('SELECT data FROM prices ORDER BY contract,day')]
    elif filename=="coverage.json":
        return dumps(store.coverage()).encode(),"application/json"
    elif filename=="analyses.jsonl":
        with store.connection() as c:
            ids = [r[0] for r in c.execute("SELECT id FROM analyses ORDER BY created_at")]
        rows = [store.analysis(identifier,include_input=True) for identifier in ids]
    else:
        raise ValueError("Unknown export")
    if filename.endswith("jsonl"):
        return ("\n".join(dumps(r) for r in rows)+"\n").encode(),"application/x-ndjson"
    buf = io.StringIO()
    keys = list(dict.fromkeys(k for r in rows for k in r)) or ["id"]
    writer = csv.DictWriter(buf,fieldnames=keys)
    writer.writeheader()
    for r in rows:
        safe = {}
        for k,v in r.items():
            if isinstance(v,(dict,list)):
                v = dumps(v)
            if isinstance(v,str) and v.lstrip().startswith(("=","+","-","@")):
                v = "'"+v
            safe[k] = v
        writer.writerow(safe)
    return ("\ufeff"+buf.getvalue()).encode(),"text/csv; charset=utf-8"
