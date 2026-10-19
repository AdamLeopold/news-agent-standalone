import hashlib
import re
import statistics
from datetime import date, timedelta
from .calendar import iso, parse_time, session_at, previous_day
from .labels import threshold

GROUPS = {
    "供应中断/复产": ["outage", "shutdown", "restart", "resume", "force majeure", "maintenance", "disruption", "halt", "unavailability", "process problems", "停产", "停供", "中断", "复产", "检修", "不可抗力"],
    "地缘政治/制裁": ["russia", "ukraine", "sanction", "war", "conflict", "nord stream", "俄罗斯", "乌克兰", "制裁", "北溪"],
    "LNG供应与流向": ["lng", "liquefaction", "regasification", "cargo", "cheniere", "freeport", "液化天然气", "到港", "再气化"],
    "库存与补库": ["storage", "inventory", "inventories", "withdrawal", "injection", "库存", "补库", "储气"],
    "天气与需求": ["weather", "cold", "warm", "heating", "demand", "winter", "天气", "寒潮", "温和", "需求", "取暖", "暖冬"],
    "政策与能源安全": ["regulation", "policy", "price cap", "energy security", "commission", "政策", "限价", "能源安全"],
}


def classify(text: str) -> list[str]:
    text = text.lower()
    return [k for k,words in GROUPS.items() if any(w in text for w in words)] or ["其他天然气新闻"]


def aggregate(items: list[dict]) -> dict:
    grouped = {}
    for n in items:
        for category in classify(str(n.get("title", ""))+" "+str(n.get("summary", ""))):
            grouped.setdefault(category,[]).append({k:n.get(k) for k in ("id","title","summary","source","url","version","published_at","published_date","timestamp_precision","first_seen_at")})
    ids = [n["id"] for n in items]
    return {"article_count":len(items), "source_count":len({n.get("source") for n in items}), "news_ids":ids, "groups":grouped,
        "summary": f"本结算窗口已收录 {len(items)} 条新闻，来自 {len({n.get('source') for n in items})} 个来源。" if items else "本结算窗口尚未收录新闻；请同时查看来源健康状态。",
        "version":hashlib.sha256(str([(n['id'],n.get('version')) for n in items]).encode()).hexdigest()[:16]}


def price_context(store, as_of: str) -> dict:
    session = session_at(as_of)
    before = previous_day(date.fromisoformat(session["trade_date"])).isoformat()
    with store.connection() as c:
        import json
        candidates = [json.loads(r[0]) for r in c.execute("SELECT data FROM prices WHERE day<=? GROUP BY contract,price_type ORDER BY json_extract(data,'$.delivery_month')",(before,))]
    if not candidates:
        return {"available":False,"verified":False,"extreme_threshold":None,"missing_fields":["previous_contract_price","sigma60"]}
    # Contract expiry/month metadata is preferred; never join two different delivery months into a return.
    future_contracts = [r for r in candidates if str(r.get("delivery_month") or r.get("contract_month") or "")[:7] > session["trade_date"][:7]]
    def expiry(r):
        if r.get("last_trading_date"):
            return date.fromisoformat(r["last_trading_date"][:10])
        from dateutil.easter import easter
        delivery = date.fromisoformat(str(r.get("delivery_month") or r.get("contract_month"))[:10])
        def uk_working(d):
            y=d.year
            closed={date(y,1,1),date(y,12,25),date(y,12,26),easter(y)-timedelta(days=2),easter(y)+timedelta(days=1)}
            if date(y,1,1).weekday()>=5:
                closed.add(date(y,1,1)+timedelta(days=7-date(y,1,1).weekday()))
            for nominal in (date(y,12,25),date(y,12,26)):
                if nominal.weekday()>=5:
                    substitute=nominal+timedelta(days=7-nominal.weekday())
                    while substitute in closed:
                        substitute+=timedelta(days=1)
                    closed.add(substitute)
            for month,last in ((5,False),(5,True),(8,True)):
                anchor=date(y,month+1,1)-timedelta(days=1) if last else date(y,month,1)
                while anchor.weekday()!=0:
                    anchor+=timedelta(days=-1 if last else 1)
                closed.add(anchor)
            return d.weekday()<5 and d not in closed
        d=delivery
        for _ in range(2):
            d-=timedelta(days=1)
            while not uk_working(d):
                d-=timedelta(days=1)
        return d
    selectable=[r for r in future_contracts if expiry(r)>date.fromisoformat(session['trade_date'])]
    if future_contracts and not selectable:
        return {"available":False,"verified":False,"extreme_threshold":None,"missing_fields":["non_expiring_front_month_contract"]}
    chosen = min(selectable,key=lambda r:str(r.get("delivery_month") or r.get("contract_month"))) if selectable else max(candidates,key=lambda r:r['day'])
    contract = chosen["contract"]
    with store.connection() as c:
        same=[json.loads(r[0]) for r in c.execute("SELECT data FROM prices WHERE contract=? AND price_type=? AND day<=? ORDER BY day",(contract,chosen['price_type'],before))]
    seen = {}
    for r in same:
        seen[r["day"]] = r
    same = list(seen.values())
    from .calendar import next_day, trading_day
    completed = [r for r in same if trading_day(date.fromisoformat(r['day']))]
    pairs = list(zip(completed,completed[1:]))[-60:]
    complete_history = len(pairs)==60 and all(next_day(date.fromisoformat(a['day'])).isoformat()==b['day'] for a,b in pairs)
    deltas = [b["value"]-a["value"] for a,b in pairs] if complete_history else []
    last = same[-1]
    return {"available":True,"contract":contract,"contract_name":last.get("contract_name",last.get('contract_label',contract)),"price_type":last["price_type"],
        "verified":bool(last.get("verified",False)),"previous_price":last["value"],"price_date":last["day"],"reference_source":last.get("provider",last.get("source","DBnomics ICE")),"source_url":last.get('source_url'),
        "extreme_threshold":threshold(deltas),"sigma60":statistics.stdev(deltas[-60:]) if len(deltas)>=60 else None,
        "threshold_frozen_as_of":as_of,"flat_delta":0.30,"observation_count":len(same),
        "risk_flags": ([] if last.get("verified") else ["价格类型尚未验证为ICE官方日结算，不用于官方结算评分"]) + ([] if last["day"]==before else ["最近价格并非上一完整交易日，存在行情缺口"]) + ([] if complete_history else ["最近60个交易日价差存在缺口或不足，未计算异常等级门槛"])}


def build_context(store, current: dict | None = None, as_of: str | None = None, kind: str = "live") -> dict:
    as_of = iso(parse_time(as_of)) if as_of else iso()
    session = session_at(as_of)
    items = store.window_news(session["trade_date"],as_of)
    imprecise=store.observed_imprecise_news(session['window_start'],as_of)
    previous = []
    day = date.fromisoformat(session["trade_date"])
    for _ in range(5):
        day = previous_day(day)
        previous.append({"trade_date":day.isoformat(),**aggregate(store.window_news(day.isoformat(),as_of))})
    text = " ".join(str(n.get("title", ""))+" "+str(n.get("summary", "")) for n in ([current] if current else [])+items+imprecise)
    events = store.retrieve_events(text,as_of)
    # The archive survives in SQLite, but future-dependent legacy outcomes never enter the prompt.
    events = [{k:v for k,v in e.items() if k not in ("legacy_outcomes","legacy_label")} for e in events]
    timing_risks=(["部分即时来源只有日期或缺少发布时间；按实际发现时刻提供当前分析背景，未伪造发布时间或写入精确结算窗口。"] if imprecise else [])
    if current and current.get('published_at') and parse_time(current['published_at'])<parse_time(session['window_start']):
        timing_risks.append('当前触发新闻在较早结算窗口发布，本次作为截至分析时点已知背景；不能把原报道重复当作本窗口新增冲击，须核对修订内容与已计价情况。')
    return {"as_of":as_of,"session":session,"current_news":current,"window_news":items+imprecise,"daily_aggregate":aggregate(items),
        "previous_sessions":previous,"historical_events":events,"price_reference":price_context(store,as_of),
        "lexicon":{"categories":GROUPS,"negation_terms":["not","no","without","unlikely","denied"],"reversal_terms":["resume","restart","restored","cancelled"]},
        "coverage":{"sources":store.sources(),"article_count":len(items),"observed_imprecise_count":len(imprecise),"risk_flags":timing_risks,"historical_timestamps":"mixed_quality"},"analysis_kind":kind}


def update_event_memory(store, news: dict):
    text = str(news.get("title", ""))+" "+str(news.get("summary", ""))
    categories = classify(text)
    severe = news.get("source")=="gassco" or any(t in text.lower() for t in ("outage","shutdown","force majeure","disruption","sanction","explosion","halt","restart","resume","commissioning","first lng","first gas"))
    if not severe:
        return
    source_event = news.get("source_event_id") or news.get("event_id") or news.get("source_message_id")
    source_revision=news.get('source_revision')
    if news.get('source')=='gassco' and source_event:
        match=re.match(r'^(.*?)[_ ]+(\d+)$',str(source_event))
        base=match[1] if match else str(source_event)
        source_revision=int(match[2]) if match else 0
        identifier='event-gassco-'+base
    else:
        identifier = "event-"+(str(source_event) if source_event else news["id"])
    confirmed=bool(re.search(r'\b(?:has|have)\s+(?:fully\s+)?(?:restarted|resumed|restored)|\b(?:restarted|resumed)\s+(?:operations|production|flows)|\boutage\s+(?:has\s+)?ended\b',text,re.I))
    uncertain=bool(re.search(r'\b(?:not|no|unconfirmed|expected|planned|will|could|might)\b.{0,50}\b(?:restart|resume|restor)\w*',text,re.I))
    partial=bool(re.search(r'\bpartial(?:ly)?\b|\bat\s+\d+(?:\.\d+)?%\s+(?:capacity|output)|\bfull\s+(?:output|capacity|production)\s+is\s+not\b|\bnot\s+(?:yet\s+)?fully\b',text,re.I))
    state = 'resolved' if confirmed and not uncertain and not partial else 'active'
    if news.get("ingestion_kind")=="backfill":
        state = "historical"
    end = start = None
    if news.get('event_start'):
        from .ingest import parse_timestamp
        start=parse_timestamp(news['event_start'],default_zone='Europe/Oslo').get('published_at')
        if start and parse_time(start)>parse_time(iso()):
            state='scheduled'
    if news.get('event_end'):
        from .ingest import parse_timestamp
        end = parse_timestamp(news['event_end'],default_zone='Europe/Oslo').get('published_at')
        if end and parse_time(end)<=parse_time(iso()):
            state='resolved'
        elif end:
            state='scheduled' if start and parse_time(start)>parse_time(iso()) else 'active'
    if str(news.get('event_status','')).lower() in ('dismissed','cancelled','inactive'):
        state='resolved'
    resolution_basis='announced_window_elapsed_unverified_flow' if news['source']=='gassco' and state=='resolved' and str(news.get('event_status','')).lower()=='active' else None
    event = {"id":identifier,"event_date":str(news.get("published_date") or news.get("published_at") or news["first_seen_at"])[:10],
        "known_at":news.get("version_seen_at",news["first_seen_at"]),"state":state,"title":news.get("title"),"summary":news.get("summary"),
        "category":categories[0],"entities":news.get("entities",[]),"news_ids":[news["id"]],"source":news["source"],"url":news["url"],
        "event_start":start,"event_end":end,"event_status":news.get('event_status'),
        "resolution_basis":resolution_basis,
        "source_quality":"source_announcement" if news["source"] not in ("offshore_energy","gdelt") else "news_report",
        "source_revision":source_revision,"source_message_id":news.get('source_message_id'),"published_at":news.get('published_at'),"outcome_available_at":None,
        "risk_flags":["重大事件候选；价格同期变化不等于事件因果影响"] + (["公告不可用容量不等于实际流量减少；未来计划事件不等于当前停供"] if news['source']=='gassco' else []) + (["公告计划时段已结束，实际流量恢复待核实"] if resolution_basis else [])}
    previous=store.get_event(identifier)
    event['change_status']='new'
    if previous:
        event['change_status']='revised' if any(previous.get(k)!=event.get(k) for k in ('title','summary','source_revision','event_end')) else previous.get('change_status','ongoing')
        event['news_ids']=list(dict.fromkeys(previous.get('news_ids',[])+event['news_ids']))
        event['known_at']=min(previous['known_at'],event['known_at'])
        if news['source']=='gassco' and int(previous.get('source_revision') or 0)>int(source_revision or 0):
            previous['news_ids']=event['news_ids']
            store.upsert_event(previous)
            return
        for field in ('outcomes','outcome_available_at'):
            if previous.get(field):
                event[field]=previous[field]
    store.upsert_event(event)
