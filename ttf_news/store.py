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


def _same_publisher_metadata(incoming: dict, previous: dict) -> bool:
    """Missing feed clocks do not retract an already observed publisher clock."""
    if any(incoming.get(key)!=previous.get(key) for key in ("title","summary")):
        return False
    def same_time(left,right):
        if not left or not right:
            return not left and not right
        try:
            return parse_time(left)==parse_time(right)
        except (ValueError,TypeError):
            return left==right
    if incoming.get("updated_at") and not same_time(incoming["updated_at"],previous.get("updated_at")):
        return False
    if incoming.get("published_at"):
        return same_time(incoming["published_at"],previous.get("published_at"))
    if incoming.get("published_date") and incoming.get("published_date")!=previous.get("published_date"):
        return False
    return True


def _price_known_at(item: dict, fallback: str) -> str:
    """Use a recorded local observation clock, never the historical price date."""
    for key in ('known_at','fetched_at','collected_at'):
        if item.get(key):
            try:
                return iso(parse_time(item[key]))
            except (ValueError,TypeError):
                continue
    return fallback


def _price_economic_identity(item: dict) -> tuple:
    # Verification and delivery identity affect eligibility for model input and
    # scoring; fetch/source-description metadata does not change an observation.
    return tuple(item.get(key) for key in ('value','verified','delivery_month','contract_month','last_trading_date','unit'))


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
              CREATE TABLE IF NOT EXISTS price_versions (contract TEXT, day TEXT, price_type TEXT,
                known_at TEXT, value REAL, data TEXT NOT NULL,
                PRIMARY KEY(contract,day,price_type,known_at));
              CREATE INDEX IF NOT EXISTS price_version_known ON price_versions(known_at,day);
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
              CREATE TABLE IF NOT EXISTS analysis_translations (id TEXT PRIMARY KEY, analysis_id TEXT, part TEXT,
                language TEXT, source_hash TEXT, status TEXT, result TEXT, error TEXT, created_at TEXT, completed_at TEXT,
                UNIQUE(analysis_id,part,language,source_hash));
              CREATE TABLE IF NOT EXISTS market_quotes (id TEXT PRIMARY KEY, contract TEXT, observed_at TEXT,
                available_at TEXT, data TEXT);
              CREATE INDEX IF NOT EXISTS market_quote_known ON market_quotes(contract,available_at,observed_at);
              CREATE TABLE IF NOT EXISTS market_points (contract TEXT, observed_at TEXT, available_at TEXT,
                value REAL, PRIMARY KEY(contract,observed_at,value));
              CREATE TABLE IF NOT EXISTS market_candles (source TEXT, contract TEXT, day TEXT,
                content_hash TEXT, available_at TEXT, data TEXT,
                PRIMARY KEY(source,contract,day,content_hash));
            ''')
            # Seed only identities that have no history yet. Preserve the old
            # prices rows/data exactly; absent local clocks become known now,
            # rather than being backdated to the observation's calendar day.
            migrated_at=iso()
            unversioned=c.execute('''SELECT p.* FROM prices p WHERE NOT EXISTS
              (SELECT 1 FROM price_versions v WHERE v.contract=p.contract
               AND v.day=p.day AND v.price_type=p.price_type)''').fetchall()
            c.executemany('INSERT OR IGNORE INTO price_versions VALUES (?,?,?,?,?,?)',
                          [(row['contract'],row['day'],row['price_type'],
                            _price_known_at(json.loads(row['data']),migrated_at),row['value'],row['data'])
                           for row in unversioned])

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
        content_fields=[item.get(k) for k in ("title","summary","updated_at","source_revision","published_at","published_date","timestamp_precision","timestamp_source")]
        if item.get('fact_excerpt'):
            content_fields.append(item['fact_excerpt'])
        digest = hashlib.sha256(dumps(content_fields).encode()).hexdigest()
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
                # A metadata-only poll has not retracted previously observed
                # public paragraphs. Keep the exact enriched version when the
                # publisher's identifying metadata is unchanged; otherwise a
                # bare RSS poll and article fetch would alternate revisions.
                same_metadata=_same_publisher_metadata(item,previous)
                same_excerpt=not item.get("fact_excerpt") or item.get("fact_excerpt")==previous.get("fact_excerpt")
                if previous.get("fact_excerpt") and same_excerpt and not item.get("is_premium") and same_metadata:
                    return {**previous,"_created":False,"_revised":False}
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

    def previous_news_version(self, identifier: str, version: int, as_of: str) -> dict | None:
        """Read the nearest lower version actually observed by the cutoff.

        Do not read the mutable latest news row or equate a publisher's revision
        clock with local availability. Parse clocks before comparison so offset
        representations cannot expose a later observation.
        """
        cutoff = parse_time(as_of)
        with self.connection() as c:
            c.execute("PRAGMA query_only=ON")
            rows = c.execute("SELECT version,known_at,data FROM news_versions WHERE news_id=? AND version<? ORDER BY version DESC",
                             (identifier, version)).fetchall()
        for row in rows:
            try:
                known = parse_time(row["known_at"])
            except (ValueError, TypeError):
                continue
            if known <= cutoff:
                return {"version": row["version"], "known_at": iso(known), "data": json.loads(row["data"])}
        return None

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

    def retrieve_events(self, text: str, as_of: str, limit: int = 6, *,
                        exclude_news_ids=None, current_event_ids=None) -> list[dict]:
        """Retrieve known atomic analogues and separately labelled ongoing risks.

        Current-window event IDs never become historical analogues. Retrieval
        does not modify the source archive, versions or original price labels.
        """
        from .retrieval import rank_events
        as_of = iso(parse_time(as_of))
        with self.connection() as c:
            versions = [json.loads(r[0]) for r in c.execute('''SELECT v.data FROM event_versions v JOIN
              (SELECT event_id,MAX(known_at) latest FROM event_versions WHERE known_at<=? GROUP BY event_id) m
              ON m.event_id=v.event_id AND m.latest=v.known_at''',(as_of,))]
        return rank_events(versions,text,parse_time(as_of),limit,
                           exclude_news_ids=exclude_news_ids,current_event_ids=current_event_ids)

    def upsert_price(self, item: dict):
        contract = str(item.get("contract") or item.get("contract_id") or item.get("series_id") or "")
        day = str(item.get("day") or item.get("date"))[:10]
        value = item.get("value", item.get("price"))
        if not contract or value is None:
            raise ValueError("A price requires contract, date and value")
        item = {**item, "contract": contract, "day": day, "value": float(value), "price_type": item.get("price_type", "reference")}
        with self.connection() as c:
            previous=c.execute('''SELECT known_at,data FROM price_versions
              WHERE contract=? AND day=? AND price_type=? ORDER BY known_at DESC LIMIT 1''',
                               (contract,day,item['price_type'])).fetchone()
            if not previous or _price_economic_identity(json.loads(previous['data']))!=_price_economic_identity(item):
                known_at=_price_known_at(item,iso())
                if previous and known_at<=previous['known_at']:
                    known_at=iso(parse_time(previous['known_at'])+timedelta(microseconds=1))
                c.execute('INSERT INTO price_versions VALUES (?,?,?,?,?,?)',
                          (contract,day,item['price_type'],known_at,item['value'],dumps(item)))
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
        result['review']=self.analysis_review(identifier)
        return result

    def analysis_protected_hashes(self, identifier: str) -> dict:
        """Hash the stored bytes used to bind an additive review to a version."""
        with self.connection() as c:
            row=c.execute('SELECT input_snapshot,result,detail_result FROM analyses WHERE id=?',(identifier,)).fetchone()
        if row is None:
            raise KeyError(identifier)
        return {key:hashlib.sha256(row[key].encode('utf-8')).hexdigest() if row[key] is not None else None
                for key in ('input_snapshot','result','detail_result')}

    def analysis_review(self, identifier: str, as_of: str | None = None) -> dict | None:
        """Read append-only review metadata without changing canonical results.

        Brief and detail applicability are independent. A detail generated
        after a brief review neither clears that review nor acquires it.
        """
        cutoff=parse_time(as_of) if as_of is not None else None
        with self.connection() as c:
            raw=c.execute('SELECT input_snapshot,result,detail_result FROM analyses WHERE id=?',(identifier,)).fetchone()
            sql="SELECT id,created_at,data FROM metrics WHERE type='analysis_review'"
            rows=c.execute(sql+(' AND julianday(created_at)<=julianday(?)' if cutoff is not None else '')+' ORDER BY id DESC',
                           (iso(cutoff),) if cutoff is not None else ()).fetchall()
        if raw is None:
            return None
        hashes={key:hashlib.sha256(raw[key].encode('utf-8')).hexdigest() if raw[key] is not None else None
                for key in ('input_snapshot','result','detail_result')}
        parts={}
        matched=[]
        for row in rows:
            if cutoff is not None:
                # SQLite's date math rounds sub-millisecond precision; retain
                # an exact aware-time check so post-cutoff reviews never leak.
                try:
                    if parse_time(row['created_at'])>cutoff:
                        continue
                except (ValueError,TypeError):
                    continue
            data=json.loads(row['data'])
            if data.get('analysis_id')!=identifier:
                continue
            stored=data.get('protected_hashes') or {}
            for part in data.get('affected_parts',[]):
                field='result' if part=='brief' else 'detail_result'
                if (part not in parts and stored.get('input_snapshot')==hashes['input_snapshot']
                        and stored.get(field) is not None and stored.get(field)==hashes[field]):
                    parts[part]={'status':data['status'],'reason_code':data['reason_code'],
                                 'message_zh':data['message_zh'],'message_en':data['message_en'],
                                 'review_id':data['review_id'],'metric_id':row['id'],'reviewed_at':row['created_at']}
            matched.append({**data,'metric_id':row['id'],'reviewed_at':row['created_at']})
        if not matched:
            return None
        invalid=[part for part,item in parts.items() if item['status']=='invalid']
        primary=parts.get('brief') or parts.get('detail') or {}
        return {'status':'invalid' if invalid else 'cleared' if parts else 'not_applicable',
                'invalid_parts':invalid,'parts':parts,'latest_review':matched[0],
                'reason_code':primary.get('reason_code'),'message_zh':primary.get('message_zh'),
                'message_en':primary.get('message_en')}

    def append_analysis_review(self, identifier: str, review: dict, *, expected_hashes: dict) -> dict:
        """Append a verified review. Call locally after deployment, never via API."""
        if review.get('status') not in ('invalid','cleared'):
            raise ValueError('Review status must be invalid or cleared')
        parts=review.get('affected_parts')
        if not isinstance(parts,list) or not parts or any(part not in ('brief','detail') for part in parts):
            raise ValueError('Review must identify brief and/or detail')
        for key in ('review_id','reason_code','message_zh','message_en','reviewer'):
            if not isinstance(review.get(key),str) or not review[key].strip():
                raise ValueError('Review requires '+key)
        if set(expected_hashes)!=set(('input_snapshot','result','detail_result')):
            raise ValueError('Review requires the complete protected field hashes')
        with self.connection() as c:
            row=c.execute('SELECT input_snapshot,result,detail_result FROM analyses WHERE id=?',(identifier,)).fetchone()
            if row is None:
                raise KeyError(identifier)
            actual={key:hashlib.sha256(row[key].encode('utf-8')).hexdigest() if row[key] is not None else None
                    for key in ('input_snapshot','result','detail_result')}
            # Only the reviewed parts must still match: a new independent
            # detail may legitimately have appeared since a brief was reviewed.
            required=['input_snapshot']+['result' if part=='brief' else 'detail_result' for part in parts]
            if any(actual[key]!=expected_hashes.get(key) for key in required):
                raise ValueError('Reviewed canonical fields changed; inspect before appending')
            if any(actual['result' if part=='brief' else 'detail_result'] is None for part in parts):
                raise ValueError('Cannot review a missing result')
            data={**review,'analysis_id':identifier,'protected_hashes':dict(expected_hashes),
                  'affected_parts':list(dict.fromkeys(parts)),'canonical_result_modified':False}
            for old in c.execute("SELECT data FROM metrics WHERE type='analysis_review'"):
                existing=json.loads(old[0])
                if existing.get('review_id')==review['review_id']:
                    if existing!=data:
                        raise ValueError('Review ID already belongs to different metadata')
                    return self.analysis_review(identifier)
            c.execute("INSERT INTO metrics(created_at,type,data) VALUES (?,'analysis_review',?)",(iso(),dumps(data)))
        self.emit('analysis_reviewed',{'id':identifier,'affected_parts':data['affected_parts'],'status':data['status']})
        return self.analysis_review(identifier)

    def latest_analysis(self, news_id: str | None = None, trade_date: str | None = None, completed_only: bool = False, live_only: bool = False, exclude_invalid: bool = False) -> dict | None:
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
            rows = c.execute("SELECT id FROM analyses"+(" WHERE "+" AND ".join(filters) if filters else "")+" ORDER BY created_at DESC"+('' if exclude_invalid else ' LIMIT 1'), args).fetchall()
        for row in rows:
            record=self.analysis(row[0])
            if not exclude_invalid or 'brief' not in ((record or {}).get('review') or {}).get('invalid_parts',[]):
                return record
        return None

    def request_detail(self, identifier: str) -> dict:
        with self.connection() as c:
            row = c.execute("SELECT status,detail_status,result FROM analyses WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise KeyError(identifier)
            if row[0] != "completed":
                raise ValueError("Wait for the short analysis to complete")
            if row[1] in ("queued", "running", "completed"):
                return {"id": identifier, "status": row[1]}
            c.execute("UPDATE analyses SET detail_status='queued',detail_error=NULL WHERE id=?", (identifier,))
            structured = bool((json.loads(row[2] or '{}').get('_meta') or {}).get('structured'))
            c.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,0,NULL)", (str(uuid.uuid4()),"detail",identifier,1 if structured else 15,"queued",iso()))
        return {"id": identifier, "status": "queued"}

    def recover_jobs(self):
        with self.connection() as c:
            c.execute("UPDATE jobs SET status='queued' WHERE status='running'")
            c.execute("UPDATE analyses SET status='queued' WHERE status='running'")
            c.execute("UPDATE analyses SET detail_status='queued' WHERE detail_status='running'")
            c.execute("UPDATE analysis_translations SET status='queued' WHERE status='running'")

    def claim_job(self) -> dict | None:
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY priority,created_at LIMIT 1").fetchone()
            if not row:
                return None
            job = dict(row)
            c.execute("UPDATE jobs SET status='running',attempts=attempts+1 WHERE id=?", (job["id"],))
            if job['kind']=='translation':
                c.execute("UPDATE analysis_translations SET status='running' WHERE id=?", (job['analysis_id'],))
            else:
                field = "detail_status" if job["kind"] == "detail" else "status"
                c.execute(f"UPDATE analyses SET {field}='running' WHERE id=?", (job["analysis_id"],))
            return job

    def finish_job(self, job: dict, result: dict | None = None, error: str | None = None):
        now = iso()
        status = "failed" if error else "completed"
        with self.connection() as c:
            if job['kind']=='translation':
                c.execute('UPDATE analysis_translations SET status=?,result=?,error=?,completed_at=? WHERE id=?',
                          (status,dumps(result) if result else None,error,now,job['analysis_id']))
            elif job["kind"] == "detail":
                c.execute("UPDATE analyses SET detail_status=?,detail_result=?,detail_error=? WHERE id=?", (status,dumps(result) if result else None,error,job["analysis_id"]))
            else:
                c.execute("UPDATE analyses SET status=?,result=?,error=?,completed_at=? WHERE id=?", (status,dumps(result) if result else None,error,now,job["analysis_id"]))
            c.execute("UPDATE jobs SET status=?,error=? WHERE id=?", (status,error,job["id"]))
        self.emit(("translation_failed" if error else "translation_completed") if job['kind']=='translation' else ("analysis_failed" if error else "analysis_completed"), {"id": job["analysis_id"], "kind": job["kind"]})

    def request_translation(self, analysis_id: str, part: str = 'brief', language: str = 'en', retry: bool = False):
        from .translation import source_hash
        if part not in ('brief','detail') or language!='en':
            raise ValueError('Unsupported translation request')
        record=self.analysis(analysis_id)
        if not record:
            raise KeyError(analysis_id)
        result=record['detail_result' if part=='detail' else 'result']
        if not result:
            raise ValueError('Wait for the original analysis to complete')
        digest=source_hash(result)
        with self.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT * FROM analysis_translations WHERE analysis_id=? AND part=? AND language=? AND source_hash=?',
                          (analysis_id,part,language,digest)).fetchone()
            if row and (row['status']!='failed' or not retry):
                return self.translation_record(row['id'])
            identifier=row['id'] if row else str(uuid.uuid4())
            if row:
                c.execute("UPDATE analysis_translations SET status='queued',error=NULL WHERE id=?",(identifier,))
            else:
                c.execute("INSERT INTO analysis_translations VALUES (?,?,?,?,?,'queued',NULL,NULL,?,NULL)",
                          (identifier,analysis_id,part,language,digest,iso()))
            structured = bool((result.get('_meta') or {}).get('structured'))
            priority = 1 if structured else (12 if part=='brief' and record['kind']=='live' else 30)
            c.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,0,NULL)",(str(uuid.uuid4()),'translation',identifier,priority,'queued',iso()))
        return self.translation_record(identifier)

    def translation_record(self, identifier: str):
        with self.connection() as c:
            row=c.execute('SELECT * FROM analysis_translations WHERE id=?',(identifier,)).fetchone()
        if not row:
            return None
        data=dict(row)
        data['result']=json.loads(data['result']) if data['result'] else None
        return data

    def cached_translation(self, analysis_id: str, part: str = 'brief'):
        from .translation import source_hash
        if part not in ('brief', 'detail'):
            raise ValueError('Unsupported translation part')
        record = self.analysis(analysis_id)
        if not record:
            raise KeyError(analysis_id)
        result = record['detail_result' if part=='detail' else 'result']
        if not result:
            return None
        with self.connection() as c:
            row = c.execute('SELECT id FROM analysis_translations WHERE analysis_id=? AND part=? AND language=? AND source_hash=?',
                            (analysis_id, part, 'en', source_hash(result))).fetchone()
        return self.translation_record(row['id']) if row else None

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
