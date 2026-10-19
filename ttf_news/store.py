from contextlib import contextmanager
from datetime import datetime, date, timedelta
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from .calendar import iso, utcnow, parse_time, session_at
from .config import DB_PATH


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str, allow_nan=False)


def canonical_url(url: str) -> str:
    p = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith("utm_") and k not in ("fbclid", "gclid")]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), urlencode(sorted(query)), ""))


class Store:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self):
        with self.connection() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript('''
              CREATE TABLE IF NOT EXISTS news (id TEXT PRIMARY KEY, url TEXT UNIQUE, source TEXT NOT NULL,
                trade_date TEXT, published_at TEXT, first_seen_at TEXT NOT NULL, content_hash TEXT NOT NULL, data TEXT NOT NULL);
              CREATE INDEX IF NOT EXISTS news_day ON news(trade_date);
              CREATE INDEX IF NOT EXISTS news_seen ON news(first_seen_at);
              CREATE TABLE IF NOT EXISTS news_versions (news_id TEXT, version INTEGER, known_at TEXT, data TEXT,
                PRIMARY KEY(news_id,version));
              CREATE TABLE IF NOT EXISTS source_states (id TEXT PRIMARY KEY, data TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS legacy_daily (id TEXT PRIMARY KEY, trade_date TEXT, data TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, event_date TEXT, known_at TEXT, updated_at TEXT,
                state TEXT, data TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS event_versions (event_id TEXT, known_at TEXT, data TEXT,
                PRIMARY KEY(event_id,known_at));
              CREATE VIRTUAL TABLE IF NOT EXISTS event_search USING fts5(id UNINDEXED, text);
              CREATE TABLE IF NOT EXISTS prices (contract TEXT, day TEXT, price_type TEXT, value REAL, data TEXT NOT NULL,
                PRIMARY KEY(contract,day,price_type));
              CREATE INDEX IF NOT EXISTS price_day ON prices(day,price_type);
              CREATE TABLE IF NOT EXISTS analyses (id TEXT PRIMARY KEY, news_id TEXT, kind TEXT, status TEXT,
                created_at TEXT, completed_at TEXT, trade_date TEXT, input_snapshot TEXT, result TEXT, error TEXT,
                detail_status TEXT DEFAULT 'not_requested', detail_result TEXT, detail_error TEXT, frozen INTEGER DEFAULT 0);
              CREATE INDEX IF NOT EXISTS analysis_news ON analyses(news_id,created_at);
              CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, kind TEXT, analysis_id TEXT, priority INTEGER,
                status TEXT, created_at TEXT, attempts INTEGER DEFAULT 0, error TEXT);
              CREATE TABLE IF NOT EXISTS daily_snapshots (trade_date TEXT PRIMARY KEY, cutoff TEXT, analysis_id TEXT, frozen_at TEXT);
              CREATE TABLE IF NOT EXISTS stream_events (id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT, created_at TEXT, payload TEXT);
              CREATE TABLE IF NOT EXISTS metrics (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, type TEXT, data TEXT);
              CREATE TABLE IF NOT EXISTS evaluations (trade_date TEXT PRIMARY KEY, analysis_id TEXT, contract TEXT, delta REAL,
                threshold REAL, actual_direction TEXT, predicted_direction TEXT, correct INTEGER, status TEXT, data TEXT);
            ''')

    def emit(self, event_type: str, payload: dict):
        with self.connection() as c:
            c.execute("INSERT INTO stream_events(type,created_at,payload) VALUES (?,?,?)", (event_type, iso(), dumps(payload)))

    def stream_since(self, cursor: int, limit: int = 100) -> list[dict]:
        with self.connection() as c:
            return [dict(r) for r in c.execute("SELECT * FROM stream_events WHERE id>? ORDER BY id LIMIT ?", (cursor,limit))]

    def upsert_news(self, item: dict) -> dict:
        item = dict(item)
        url = canonical_url(item.get("url", ""))
        if not url:
            raise ValueError("Collected news requires a source URL")
        identifier = item.get("id") or hashlib.sha256(url.encode()).hexdigest()[:24]
        now = iso()
        digest = hashlib.sha256(dumps([item.get(k) for k in ("title","summary","updated_at","source_revision","published_at","published_date","timestamp_precision","timestamp_source")]).encode()).hexdigest()
        published = item.get("published_at")
        if published:
            try:
                published = iso(parse_time(published))
                item["trade_date"] = session_at(published)["trade_date"]
            except (ValueError, TypeError):
                item["timestamp_precision"] = "unknown"
                published = None
        # Date-only history remains unassigned; no noon/midnight surrogate is introduced.
        if not published:
            item["trade_date"] = None
        item.update(id=identifier, url=url, published_at=published, content_hash=digest)
        created = revised = False
        with self.connection() as c:
            old = c.execute("SELECT * FROM news WHERE url=?", (url,)).fetchone()
            if old:
                previous = json.loads(old["data"])
                identifier = old["id"]
                item.update(id=identifier, first_seen_at=previous["first_seen_at"], version=previous.get("version",1))
                revised = old["content_hash"] != digest
                # A repeated archive crawl must not downgrade a record's timestamp or provenance.
                if not published and previous.get("published_at"):
                    item.update(published_at=previous["published_at"], trade_date=previous.get("trade_date"), timestamp_precision=previous.get("timestamp_precision"))
                if previous.get("ingestion_kind") == "live":
                    item["ingestion_kind"] = "live"
                if not revised:
                    return {**previous, "_created": False, "_revised": False}
                item["version"] += 1
            else:
                created = True
                item.setdefault("first_seen_at", now)
                item["version"] = 1
            item["version_seen_at"] = now
            item.setdefault("summary_created_at", now)
            item.setdefault("timestamp_precision", "exact" if published else "unknown")
            item.setdefault("ingestion_kind", "live")
            item.setdefault("category", "其他天然气新闻")
            item.setdefault("entities", [])
            c.execute("INSERT OR REPLACE INTO news VALUES (?,?,?,?,?,?,?,?)", (identifier,url,item["source"],item.get("trade_date"),item.get("published_at"),item["first_seen_at"],digest,dumps(item)))
            c.execute("INSERT INTO news_versions VALUES (?,?,?,?)", (identifier,item["version"],now,dumps(item)))
        self.emit("news_created" if created else "news_revised", {"id": identifier, "ingestion_kind": item["ingestion_kind"]})
        return {**item, "_created": created, "_revised": revised}

    def get_news(self, identifier: str) -> dict | None:
        with self.connection() as c:
            row = c.execute("SELECT data FROM news WHERE id=?", (identifier,)).fetchone()
        return json.loads(row[0]) if row else None

    def news(self, limit: int = 50, offset: int = 0, trade_date: str | None = None, scope: str = "all") -> list[dict]:
        with self.connection() as c:
            sql, args = "SELECT data FROM news", []
            conditions = []
            if trade_date:
                conditions.append("trade_date=?")
                args.append(trade_date)
            if scope=="recent":
                conditions.append("(published_at>=? OR (published_at IS NULL AND json_extract(data,'$.published_date')>=?))")
                since = (utcnow()-timedelta(days=7))
                args += [iso(since),since.date().isoformat()]
            elif scope=="live":
                conditions.append("json_extract(data,'$.ingestion_kind')='live'")
            if conditions:
                sql += " WHERE "+" AND ".join(conditions)
            sql += " ORDER BY first_seen_at DESC, rowid DESC LIMIT ? OFFSET ?"
            rows = c.execute(sql, (*args,limit,offset))
            return [json.loads(r[0]) for r in rows]

    def window_news(self, trade_date: str, as_of: str, historical: bool = False) -> list[dict]:
        as_of = iso(parse_time(as_of))
        with self.connection() as c:
            if historical:
                return [json.loads(r[0]) for r in c.execute("SELECT data FROM news WHERE trade_date=? ORDER BY published_at", (trade_date,))]
            # Retrieve the version actually known at as_of, not a later revision.
            rows = c.execute('''SELECT nv.data FROM news_versions nv JOIN
                (SELECT news_id,MAX(version) version FROM news_versions WHERE known_at<=? GROUP BY news_id) v
                ON nv.news_id=v.news_id AND nv.version=v.version''', (as_of,))
            return [n for r in rows if (n := json.loads(r[0])).get("trade_date") == trade_date and n.get("first_seen_at", "9999") <= as_of]

    def get_source_state(self, identifier: str) -> dict:
        with self.connection() as c:
            row = c.execute("SELECT data FROM source_states WHERE id=?", (identifier,)).fetchone()
        return json.loads(row[0]) if row else {}

    def observed_imprecise_news(self,start:str,as_of:str) -> list[dict]:
        start,as_of=iso(parse_time(start)),iso(parse_time(as_of))
        from .calendar import EUROPE
        local_day=parse_time(as_of).astimezone(EUROPE).date().isoformat()
        with self.connection() as c:
            rows=c.execute('''SELECT nv.data FROM news_versions nv JOIN
                (SELECT news_id,MAX(version) version FROM news_versions WHERE known_at<=? GROUP BY news_id) v
                ON nv.news_id=v.news_id AND nv.version=v.version''',(as_of,))
            items=[json.loads(r[0]) for r in rows]
        return [{**n,'window_membership':'first_observed_in_window_publication_window_unknown'} for n in items
            if not n.get('published_at') and n.get('ingestion_kind')=='live' and start<=n.get('first_seen_at','')<=as_of
            and (not n.get('published_date') or n['published_date']<=local_day)]

    def set_source_state(self, identifier: str, state: dict):
        state = {**self.get_source_state(identifier), **state, "id": identifier, "source": identifier}
        with self.connection() as c:
            c.execute("INSERT OR REPLACE INTO source_states VALUES (?,?)", (identifier,dumps(state)))
        self.emit("source_update", {"id": identifier})

    def sources(self) -> list[dict]:
        with self.connection() as c:
            return [json.loads(r[0]) for r in c.execute("SELECT data FROM source_states ORDER BY id")]

    def upsert_event(self, event: dict):
        event = dict(event)
        identifier = event.setdefault("id", str(uuid.uuid4()))
        event.setdefault("known_at", iso())
        event.setdefault("state", "active")
        with self.connection() as c:
            prior=c.execute('SELECT data FROM events WHERE id=?',(identifier,)).fetchone()
        if prior:
            prior=json.loads(prior[0])
            if {k:v for k,v in prior.items() if k!='updated_at'}=={k:v for k,v in event.items() if k!='updated_at'}:
                return
        event["updated_at"] = iso()
        searchable = " ".join(str(event.get(k, "")) for k in ("title", "summary", "category", "entities", "matched_terms"))
        with self.connection() as c:
            c.execute("INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?)", (identifier,event.get("event_date"),event["known_at"],event["updated_at"],event["state"],dumps(event)))
            c.execute("INSERT OR REPLACE INTO event_versions VALUES (?,?,?)", (identifier,event["updated_at"],dumps(event)))
            c.execute("DELETE FROM event_search WHERE id=?", (identifier,))
            c.execute("INSERT INTO event_search(id,text) VALUES (?,?)", (identifier,searchable))

    def events(self, limit: int = 100) -> list[dict]:
        with self.connection() as c:
            return [json.loads(r[0]) for r in c.execute("SELECT data FROM events WHERE state!='superseded' ORDER BY event_date DESC LIMIT ?", (limit,))]

    def get_event(self,identifier:str) -> dict | None:
        with self.connection() as c:
            row=c.execute('SELECT data FROM events WHERE id=?',(identifier,)).fetchone()
        return json.loads(row[0]) if row else None

    def retrieve_events(self, text: str, as_of: str, limit: int = 6) -> list[dict]:
        as_of = iso(parse_time(as_of))
        tokens = list(dict.fromkeys(re.findall(r"[a-zA-Z]{3,}|[\u4e00-\u9fff]{2,6}", text.lower())))[:24]
        # Deterministic bilingual entities/mechanisms support Chinese manual input against English archives.
        aliases={'挪威':['norway','norwegian','gassco'],'停产':['outage','shutdown','unavailability'],'停供':['outage','unavailability'],'中断':['disruption','outage'],'复产':['restart','resume'],
            '温和':['mild','warm'],'暖冬':['mild','warm'],'库存':['storage','inventory'],'制裁':['sanction'],'俄罗斯':['russia'],'乌克兰':['ukraine'],'到港':['lng','cargo']}
        for term,related in aliases.items():
            if term in text.lower():
                tokens.extend(related)
        tokens=list(dict.fromkeys(tokens))
        stop = {"the", "and", "for", "with", "that", "this", "news", "gas", "natural", "from", "will", "have"}
        query = " OR ".join('"'+v+'"' for v in tokens if v not in stop)
        with self.connection() as c:
            versions = [json.loads(r[0]) for r in c.execute('''SELECT v.data FROM event_versions v JOIN
              (SELECT event_id,MAX(known_at) latest FROM event_versions WHERE known_at<=? GROUP BY event_id) m
              ON m.event_id=v.event_id AND m.latest=v.known_at''',(as_of,))]
        eligible = [e for e in versions if e.get("known_at", "9999") <= as_of and e.get('state')!='superseded']
        terms = [t for t in tokens if t not in stop]
        def relevance(e):
            title=str(e.get('title','')).lower()
            content=" ".join(str(e.get(k,'')) for k in ('summary','category','entities','matched_terms')).lower()
            score=sum(2*(t in title)+(t in content) for t in terms)
            # Daily legacy bundles contain dozens of unrelated headlines; avoid letting length alone win.
            if e.get('source')=='legacy_daily':
                score*=.65
            return score
        active = sorted([e for e in eligible if e.get("state")=="active"],key=lambda e:(relevance(e),e.get("event_date","")),reverse=True)[:min(2,limit)]
        # Reserve room for ongoing risks rather than filling every slot with old analogues.
        hits = sorted([e for e in eligible if relevance(e)>0],key=lambda e:(relevance(e),e.get("event_date","")),reverse=True)[:max(0,limit-len(active))]
        ids = {e["id"] for e in hits}
        for event in active:
            if event["id"] not in ids:
                event["retrieval_role"] = "active_background"
                hits.append(event)
                ids.add(event['id'])
        for event in sorted(eligible,key=lambda e:(relevance(e),e.get('event_date','')),reverse=True):
            if len(hits)>=limit:
                break
            if event['id'] not in ids and relevance(event)>0:
                hits.append(event)
                ids.add(event['id'])
        return hits

    def upsert_price(self, item: dict):
        contract = str(item.get("contract") or item.get("contract_id") or item.get("series_id") or "")
        day = str(item.get("day") or item.get("date"))[:10]
        value = item.get("value", item.get("price"))
        if not contract or value is None:
            raise ValueError("A price requires contract, date and value")
        item = {**item, "contract": contract, "day": day, "value": float(value), "price_type": item.get("price_type", "reference")}
        with self.connection() as c:
            c.execute("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?)", (contract,day,item["price_type"],item["value"],dumps(item)))

    def create_analysis(self, context: dict, kind: str, news_id: str | None = None, priority: int = 10) -> dict:
        identifier = str(uuid.uuid4())
        now = iso()
        with self.connection() as c:
            c.execute("INSERT INTO analyses(id,news_id,kind,status,created_at,trade_date,input_snapshot) VALUES (?,?,?,'queued',?,?,?)", (identifier,news_id,kind,now,context["session"]["trade_date"],dumps(context)))
            c.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,0,NULL)", (str(uuid.uuid4()),"short",identifier,priority,"queued",now))
        self.emit("analysis_queued", {"id": identifier})
        return {"id": identifier, "status": "queued"}

    def analysis(self, identifier: str, include_input: bool = False) -> dict | None:
        with self.connection() as c:
            row = c.execute("SELECT * FROM analyses WHERE id=?", (identifier,)).fetchone()
        if not row:
            return None
        result = dict(row)
        for key in ("result", "detail_result", "input_snapshot"):
            result[key] = json.loads(result[key]) if result[key] else None
        if not include_input:
            result.pop("input_snapshot", None)
        return result

    def latest_analysis(self, news_id: str | None = None, trade_date: str | None = None, completed_only: bool = False, live_only: bool = False) -> dict | None:
        filters, args = [], []
        if news_id:
            filters.append("news_id=?"); args.append(news_id)
        if trade_date:
            filters.append("trade_date=?"); args.append(trade_date)
        if completed_only:
            filters.append("status='completed'")
        if live_only:
            filters.append("kind='live'")
        with self.connection() as c:
            row = c.execute("SELECT id FROM analyses"+(" WHERE "+" AND ".join(filters) if filters else "")+" ORDER BY created_at DESC LIMIT 1", args).fetchone()
        return self.analysis(row[0]) if row else None

    def request_detail(self, identifier: str) -> dict:
        with self.connection() as c:
            row = c.execute("SELECT status,detail_status FROM analyses WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise KeyError(identifier)
            if row[0] != "completed":
                raise ValueError("Wait for the short analysis to complete")
            if row[1] in ("queued", "running", "completed"):
                return {"id": identifier, "status": row[1]}
            c.execute("UPDATE analyses SET detail_status='queued',detail_error=NULL WHERE id=?", (identifier,))
            c.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,0,NULL)", (str(uuid.uuid4()),"detail",identifier,15,"queued",iso()))
        return {"id": identifier, "status": "queued"}

    def recover_jobs(self):
        with self.connection() as c:
            c.execute("UPDATE jobs SET status='queued' WHERE status='running'")
            c.execute("UPDATE analyses SET status='queued' WHERE status='running'")
            c.execute("UPDATE analyses SET detail_status='queued' WHERE detail_status='running'")

    def claim_job(self) -> dict | None:
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY priority,created_at LIMIT 1").fetchone()
            if not row:
                return None
            job = dict(row)
            c.execute("UPDATE jobs SET status='running',attempts=attempts+1 WHERE id=?", (job["id"],))
            field = "detail_status" if job["kind"] == "detail" else "status"
            c.execute(f"UPDATE analyses SET {field}='running' WHERE id=?", (job["analysis_id"],))
            return job

    def finish_job(self, job: dict, result: dict | None = None, error: str | None = None):
        now = iso()
        status = "failed" if error else "completed"
        with self.connection() as c:
            if job["kind"] == "detail":
                c.execute("UPDATE analyses SET detail_status=?,detail_result=?,detail_error=? WHERE id=?", (status,dumps(result) if result else None,error,job["analysis_id"]))
            else:
                c.execute("UPDATE analyses SET status=?,result=?,error=?,completed_at=? WHERE id=?", (status,dumps(result) if result else None,error,now,job["analysis_id"]))
            c.execute("UPDATE jobs SET status=?,error=? WHERE id=?", (status,error,job["id"]))
        self.emit("analysis_failed" if error else "analysis_completed", {"id": job["analysis_id"], "kind": job["kind"]})

    def metric(self, kind: str, data: dict):
        with self.connection() as c:
            c.execute("INSERT INTO metrics(created_at,type,data) VALUES (?,?,?)", (iso(),kind,dumps(data)))

    def counts(self) -> dict:
        with self.connection() as c:
            return {name: c.execute(f"SELECT COUNT(*) FROM {name}"+(" WHERE state!='superseded'" if name=='events' else '')).fetchone()[0] for name in ("news","events","legacy_daily","prices","analyses")}

    def coverage(self) -> dict:
        with self.connection() as c:
            rows = [json.loads(r[0]) for r in c.execute("SELECT data FROM news")]
        months = {}
        for n in rows:
            month = str(n.get("published_date") or n.get("published_at") or "unknown")[:7]
            key = (n["source"],month)
            rec = months.setdefault(key,{"source":n["source"],"month":month,"count":0,"exact":0,"date":0,"unknown":0})
            rec["count"] += 1
            precision = n.get("timestamp_precision", "unknown")
            rec[precision if precision in ("exact","date") else "unknown"] += 1
        return {"totals": self.counts(), "sources": self.sources(), "by_month": sorted(months.values(),key=lambda r:(r["source"],r["month"])), "generated_at": iso(),
            "notes": ["未出现月份不等于已确认无新闻；请结合来源历史抓取状态。", "只有日期的历史新闻不分配到精确结算窗口。", "历史回补的首次发现时间为实际采集时间，不是历史可用时间。"]}
