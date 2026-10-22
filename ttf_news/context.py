import copy
import hashlib
import re
import statistics
from decimal import Decimal, InvalidOperation
from datetime import date, timedelta
from .calendar import iso, parse_time, session_at, previous_day
from .labels import threshold
from .coverage import news_source_health

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
            grouped.setdefault(category,[]).append({k:n.get(k) for k in ("id","title","summary","fact_excerpt","source","url","version","published_at","published_date","timestamp_precision","first_seen_at")})
    ids = [n["id"] for n in items]
    return {"article_count":len(items), "source_count":len({n.get("source") for n in items}), "news_ids":ids, "groups":grouped,
        "summary": f"本结算窗口已收录 {len(items)} 条新闻，来自 {len({n.get('source') for n in items})} 个来源。" if items else "本结算窗口尚未收录新闻；请同时查看来源健康状态。",
        "version":hashlib.sha256(str([(n['id'],n.get('version')) for n in items]).encode()).hexdigest()[:16]}


def settlement_anchor(reference: dict, session: dict, as_of: str) -> dict:
    """Identify the target's previous trading-day contract reference exactly."""
    import math
    target=session.get('trade_date') or session.get('session_date')
    if not target:
        target=session_at(as_of)['trade_date']
    expected=previous_day(date.fromisoformat(str(target)[:10])).isoformat()
    result={'available':False,'target_trade_date':target,'expected_previous_trade_date':expected,
            'reference_date':reference.get('price_date'),'contract':reference.get('contract'),
            'known_time_precision':'exact' if reference.get('price_known_at') else 'not_provided',
            'scope':'仅目标日前一交易日同合约参考价可作前结算比较；其他日价不是昨收'}
    if reference.get('price_date')!=expected:
        return {**result,'reason':'previous_trading_day_reference_missing'}
    try:
        value=reference['previous_price']
        if isinstance(value,bool) or not math.isfinite(float(value)) or float(value)<=0:
            return {**result,'reason':'invalid_previous_reference_value'}
        if not reference.get('contract') or reference.get('available') is False:
            return {**result,'reason':'previous_contract_reference_missing'}
        if any(reference.get(key) and reference[key]!=reference['contract'] for key in ('previous_contract','previous_price_contract','reference_contract')):
            return {**result,'reason':'previous_contract_mismatch'}
        now=parse_time(as_of)
        for key in ('price_known_at','known_at','available_at','fetched_at','version_seen_at'):
            if reference.get(key) and parse_time(reference[key])>now:
                return {**result,'reason':'previous_reference_not_known_as_of'}
    except (KeyError,ValueError,TypeError):
        return {**result,'reason':'invalid_previous_reference_identity'}
    return {**result,'available':True,'reason':'exact_previous_trading_day_same_contract'}


def model_price_reference(reference: dict, session: dict, as_of: str) -> dict:
    """Keep quote observations; expose only a legal previous-day comparison.

    Full historical values and provider payloads stay in the immutable input
    snapshot. An older daily reference is intentionally not an arithmetic
    operand in a model request, even when its date could be labelled.
    """
    value=copy.deepcopy(reference or {})
    anchor=settlement_anchor(value,session,as_of)
    original_baseline=value.get('market_baseline') or {}
    baseline_eligible=bool(anchor['available'] and original_baseline.get('available')
                           and original_baseline.get('contract')==value.get('contract')
                           and (not (value.get('intraday') or {}).get('contract') or value['intraday']['contract']==value.get('contract')))
    comparison_keys={'previous_price','previous_close','prev_close','previous_settlement','prior_close','prior_price',
                     'change','change_pct','change_percent','percent_change','percentage_change','net_change','price_change',
                     'delta','pct_change','direction','observed_delta','observed_direction','change_from_previous_close'}
    def clean(node, *, canonical_baseline=False):
        if isinstance(node,list):return [clean(row) for row in node]
        if not isinstance(node,dict):return node
        result={}
        for key,row in node.items():
            if key in {'announcement_price_reaction','priced_in_evidence'}:
                result[key]=copy.deepcopy(row)  # Separate comparable event evidence.
            elif key=='settlement_anchor':
                continue
            elif (key in comparison_keys or re.search(r'change|direction|delta|(?:previous|prior|prev|yesterday).*(?:price|close|settlement|reference)|lastclose|preclose',re.sub(r'[^a-z0-9]','',key.lower()))) and key!='flat_delta' and not (canonical_baseline and baseline_eligible):
                continue
            else:
                result[key]=clean(row,canonical_baseline=(key=='market_baseline'))
        return result
    cleaned=clean(value)
    if baseline_eligible:
        # This is the vetted canonical same-contract/day comparison. Preserve
        # it exactly; the whitelist below applies only to unusable baselines.
        cleaned['market_baseline']=copy.deepcopy(original_baseline)
    elif original_baseline:
        # An unusable baseline is not an extensible provider payload. Unknown
        # aliases such as reference_move (including nested/camel variants)
        # cannot become a second route for the rejected comparison operand.
        observed_fields=('kind','contract','as_of','observed_at','available_at','latest_price',
                         'price_type','source_url','minimum_delay_minutes','verified_settlement')
        cleaned['market_baseline']={key:copy.deepcopy(original_baseline[key]) for key in observed_fields if key in original_baseline}
        cleaned['market_baseline'].update(available=False,
            reason=anchor['reason'] if not anchor['available'] else original_baseline.get('reason') or 'unusable_market_baseline',
            interpretation='仅观察价位与时钟；不构成相对目标日前一交易日的价格变化')
    if anchor['available']:
        cleaned['previous_price']=value['previous_price']
        if original_baseline.get('available') and not baseline_eligible:
            cleaned['market_baseline']={**(cleaned.get('market_baseline') or {}),'available':False,'reason':'contract_mismatch'}
    else:
        # Keep the historical date/scope for provenance, without the value
        # that generated the incorrect multi-day 'previous settlement' delta.
        cleaned.pop('price_date',None)
        cleaned.pop('price_known_at',None)
        cleaned['historical_reference_scope']={'reference_date':value.get('price_date'),'contract':value.get('contract'),
                                             'reason':anchor['reason'],'numeric_value_location':'原始input_snapshot及公共日价记录；不作为本次前结算锚点'}
        baseline=cleaned.get('market_baseline') or {}
        cleaned['market_baseline']={**baseline,'available':False,'reason':anchor['reason']}
    cleaned['settlement_anchor']=anchor
    return cleaned


def price_context(store, as_of: str, *, reference_trade_date: str | None = None) -> dict:
    as_of=iso(parse_time(as_of))
    if reference_trade_date is not None:
        from .calendar import session_for_day
        session=session_for_day(reference_trade_date)
    else:
        session = session_at(as_of)
    before = previous_day(date.fromisoformat(session["trade_date"])).isoformat()
    with store.connection() as c:
        import json
        rows=c.execute('''SELECT v.data,v.known_at FROM price_versions v JOIN
          (SELECT contract,price_type,MAX(day) latest_day FROM price_versions
           WHERE day<=? AND known_at<=? GROUP BY contract,price_type) d
          ON v.contract=d.contract AND v.price_type=d.price_type AND v.day=d.latest_day
          WHERE v.known_at=(SELECT MAX(k.known_at) FROM price_versions k
            WHERE k.contract=v.contract AND k.day=v.day AND k.price_type=v.price_type AND k.known_at<=?)''',
                       (before,as_of,as_of))
        candidates=[{**json.loads(r['data']),'known_at':r['known_at']} for r in rows]
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
        same=[{**json.loads(r['data']),'known_at':r['known_at']} for r in c.execute('''
          SELECT v.data,v.known_at FROM price_versions v JOIN
          (SELECT day,MAX(known_at) latest FROM price_versions
           WHERE contract=? AND price_type=? AND day<=? AND known_at<=? GROUP BY day) k
          ON v.day=k.day AND v.known_at=k.latest
          WHERE v.contract=? AND v.price_type=? ORDER BY v.day''',
            (contract,chosen['price_type'],before,as_of,contract,chosen['price_type']))]
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
    from .market import known_quote
    intraday = None if reference_trade_date is not None else known_quote(store, contract, as_of)
    result = {"available":True,"contract":contract,"contract_name":last.get("contract_name",last.get('contract_label',contract)),"price_type":last["price_type"],
        "intraday":intraday,
        "verified":bool(last.get("verified",False)),"previous_price":last["value"],"price_date":last["day"],"price_known_at":last['known_at'],"reference_source":last.get("provider",last.get("source","DBnomics ICE")),"source_url":last.get('source_url'),
        "extreme_threshold":threshold(deltas),"sigma60":statistics.stdev(deltas[-60:]) if len(deltas)>=60 else None,
        "threshold_frozen_as_of":as_of,"flat_delta":0.30,"observation_count":len(same),
        "risk_flags": ([] if last.get("verified") else ["价格类型尚未验证为ICE官方日结算，不用于官方结算评分"]) + ([] if last["day"]==before else ["最近价格并非上一完整交易日，存在行情缺口"]) + ([] if complete_history else ["最近60个交易日价差存在缺口或不足，未计算异常等级门槛"])}
    if reference_trade_date is not None:
        result.update(reference_trade_date=session['trade_date'],selection_mode='retrospective_contract_selection')
        result['market_baseline']={'available':False,'reason':'retrospective_contract_selection',
                                  'contract':contract,'as_of':as_of,'verified_settlement':False}
    else:
        result['market_baseline'] = market_baseline(result,session,as_of)
    result['settlement_anchor']=settlement_anchor(result,session,as_of)
    return result


def market_baseline(reference: dict, session: dict, as_of: str) -> dict:
    """Known same-contract price path; never an evaluated settlement outcome."""
    import math
    quote=reference.get('intraday') or {}
    base={'available':False,'kind':'observed_reference_move','contract':reference.get('contract'),
          'as_of':as_of,'verified_settlement':False,'flat_band':0.30,
          'interpretation':'已观察同合约参考变化；延续或反转均为待验证情景，不是目标日已实现结算'}
    anchor=settlement_anchor(reference,session,as_of)
    if not anchor['available']:
        return {**base,'reason':anchor['reason']}
    if not quote or quote.get('stale'):
        return {**base,'reason':'fresh_intraday_quote_missing'}
    if quote.get('contract')!=reference.get('contract'):
        return {**base,'reason':'contract_mismatch'}
    try:
        when,available,now=map(parse_time,(quote['observed_at'],quote['available_at'],as_of))
        if max(when,available)>now or when<parse_time(session['window_start']):
            return {**base,'reason':'quote_outside_known_window'}
        if (now-when).total_seconds()>3600:
            return {**base,'reason':'quote_too_old'}
        prior,current=float(reference['previous_price']),float(quote['value'])
        if not all(math.isfinite(v) and v>0 for v in (prior,current)):
            return {**base,'reason':'invalid_reference_value'}
    except (ValueError,KeyError,TypeError):
        return {**base,'reason':'incomplete_price_identity'}
    move=current-prior
    from .labels import actual_label
    direction=actual_label(move,reference.get('extreme_threshold'))
    if direction is None:
        direction='up' if move>0.30 else 'down' if move<-.30 else 'flat'
    return {**base,'available':True,'previous_price':prior,'previous_date':reference['price_date'],
            'latest_price':current,'observed_delta':round(move,6),'observed_direction':direction,
            'observed_at':quote['observed_at'],'available_at':quote['available_at'],
            'price_type':quote.get('price_type'),'source_url':quote.get('source_url'),
            'reference_source_url':reference.get('source_url'),'minimum_delay_minutes':quote.get('minimum_delay_minutes'),
            'extreme_threshold':reference.get('extreme_threshold')}


REVISION_FACT_FIELDS = ("title", "summary", "fact_excerpt", "event_start", "event_end", "event_status",
                        "technical_capacity", "available_capacity", "unavailable_capacity", "capacity_unit",
                        "notice_type", "unavailability_type", "event_type", "source_reason", "reason", "remarks",
                        "source_message", "source_message_text", "operational_message", "entities")
REVISION_METADATA_FIELDS = ("source_message_id", "source_event_id", "source_revision", "updated_at",
                            "published_at", "published_date", "timestamp_precision", "timestamp_source",
                            "version_seen_at", "summary_created_at", "fact_excerpt_created_at")


def _revision_facts(news: dict) -> dict:
    from .evidence import exact_capacity_fields
    return {**{key: news.get(key) for key in REVISION_FACT_FIELDS}, **exact_capacity_fields(news)}


def _revision_value(field: str, value, *, source: str):
    """Compare content, excluding clocks/notice IDs embedded in UMM metadata."""
    if field == "summary" and source == "gassco":
        from .evidence import source_clauses
        return " ".join(source_clauses(str(value or ""))).strip()
    if field in ("event_start", "event_end") and value:
        from .ingest import parse_timestamp
        parsed = parse_timestamp(str(value), default_zone="Europe/Oslo").get("published_at")
        return parsed or value
    if field in ("technical_capacity", "available_capacity", "unavailable_capacity") and value is not None:
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return value
    if field == "entities" and isinstance(value, list):
        return sorted(str(entity).strip().casefold() for entity in value)
    if isinstance(value, str):
        value = " ".join(value.split())
        return value.casefold() if field in ("event_status", "capacity_unit") else value
    return value


def enrich_news_revision(store, news: dict | None, as_of: str) -> dict | None:
    """Copy a news input and attach observed prior-version facts, without writes.

    Capacity deltas describe announcement constraints. They do not report a new
    physical flow loss, recovery, or price direction. Existing snapshots and the
    caller's news object are never changed.
    """
    if news is None:
        return None
    item = copy.deepcopy(news)
    item.pop("revision_delta", None)
    try:
        version = int(item.get("version", 1))
    except (TypeError, ValueError):
        return item
    if version <= 1 or not item.get("id"):
        return item
    cutoff = parse_time(as_of)
    delta = {"previous_version": None, "previous_known_at": None, "changed_fields": {},
             "substantive_change": None, "comparison_status": "previous_version_unavailable"}
    for clock in ("version_seen_at", "summary_created_at", "fact_excerpt_created_at"):
        if item.get(clock):
            try:
                if parse_time(item[clock]) > cutoff:
                    delta["comparison_status"] = "current_version_not_known_as_of"
                    item["revision_delta"] = delta
                    return item
            except (TypeError, ValueError):
                delta["comparison_status"] = "current_version_availability_unknown"
                item["revision_delta"] = delta
                return item
    previous = store.previous_news_version(item["id"], version, as_of)
    if previous is None:
        item["revision_delta"] = delta
        return item
    before = previous["data"]
    old_facts, new_facts = _revision_facts(before), _revision_facts(item)
    source = str(item.get("source") or before.get("source") or "")
    changes = {field: {"before": old_facts.get(field), "after": new_facts.get(field)}
               for field in REVISION_FACT_FIELDS
               if _revision_value(field, old_facts.get(field), source=source) !=
                  _revision_value(field, new_facts.get(field), source=source)}
    delta.update(previous_version=previous["version"], previous_known_at=previous["known_at"],
                 changed_fields=changes, substantive_change=bool(changes), comparison_status="compared")
    # Immutable observed version metadata, separate from publication/first-seen.
    # This copied bundle never updates the news row or invents a publisher clock.
    def revision_basis(record):
        return {"source": record.get("source"), "timestamp_source": record.get("timestamp_source"),
                "publisher_article_id": record.get("publisher_article_id"),
                "updated_at": record.get("updated_at"),
                "excerpt_method": (record.get("fact_excerpt_provenance") or {}).get("method"),
                "excerpt_status": record.get("fact_excerpt_status")}
    delta["source_version_metadata"] = {"before": revision_basis(before), "after": revision_basis(item)}
    metadata = [field for field in REVISION_METADATA_FIELDS if before.get(field) != item.get(field)]
    if metadata:
        delta["metadata_changed_fields"] = metadata
    capacities = {}
    unit = new_facts.get("capacity_unit") or old_facts.get("capacity_unit")
    same_unit = _revision_value("capacity_unit", old_facts.get("capacity_unit"), source=source) == _revision_value("capacity_unit", new_facts.get("capacity_unit"), source=source)
    for field in ("technical_capacity", "available_capacity", "unavailable_capacity"):
        if field not in changes or not same_unit:
            continue
        try:
            old, new = Decimal(str(old_facts[field])), Decimal(str(new_facts[field]))
            if not old.is_finite() or not new.is_finite():
                continue
        except (InvalidOperation, TypeError):
            continue
        change = new-old
        capacities[field] = {**changes[field], "delta": format(change.normalize(), "f"), "unit": unit,
                             "interpretation": "announced_capacity_change_not_actual_flow"}
        if field == "unavailable_capacity":
            capacities[field]["constraint_change"] = "decreased" if change < 0 else "increased" if change > 0 else "unchanged"
    if capacities:
        delta["capacity_changes"] = capacities
    item["revision_delta"] = delta
    return item


def build_context(store, current: dict | None = None, as_of: str | None = None, kind: str = "live") -> dict:
    as_of = iso(parse_time(as_of)) if as_of else iso()
    session = session_at(as_of)
    enriched = {}
    def revise(news):
        if news is None:
            return None
        key = (news.get("id"), news.get("version"), news.get("content_hash"))
        if key not in enriched:
            enriched[key] = enrich_news_revision(store, news, as_of)
        return enriched[key]
    current = revise(current)
    items = [revise(news) for news in store.window_news(session["trade_date"],as_of)]
    imprecise=[revise(news) for news in store.observed_imprecise_news(session['window_start'],as_of)]
    previous = []
    day = date.fromisoformat(session["trade_date"])
    for _ in range(5):
        day = previous_day(day)
        previous.append({"trade_date":day.isoformat(),**aggregate(store.window_news(day.isoformat(),as_of))})
    text = " ".join(str(n.get("title", ""))+" "+str(n.get("summary", ""))+" "+str(n.get('fact_excerpt') or '') for n in ([current] if current else [])+items+imprecise)
    events = store.retrieve_events(text,as_of,exclude_news_ids=[n.get('id') for n in ([current] if current else [])+items+imprecise if n.get('id')])
    # The archive survives in SQLite, but future-dependent legacy outcomes never enter the prompt.
    events = [{k:v for k,v in e.items() if k not in ("legacy_outcomes","legacy_label")} for e in events]
    timing_risks=(["部分即时来源只有日期或缺少发布时间；按实际发现时刻提供当前分析背景，未伪造发布时间或写入精确结算窗口。"] if imprecise else [])
    if current and current.get('published_at') and parse_time(current['published_at'])<parse_time(session['window_start']):
        timing_risks.append('当前触发新闻在较早结算窗口发布，本次作为截至分析时点已知背景；不能把原报道重复当作本窗口新增冲击，须核对修订内容与已计价情况。')
    return {"as_of":as_of,"session":session,"current_news":current,"window_news":items+imprecise,"daily_aggregate":aggregate(items),
        "previous_sessions":previous,"historical_events":events,"price_reference":price_context(store,as_of),
        "lexicon":{"categories":GROUPS,"negation_terms":["not","no","without","unlikely","denied"],"reversal_terms":["resume","restart","restored","cancelled"]},
        "coverage":{"sources":news_source_health(store.sources(),as_of),"article_count":len(items),"observed_imprecise_count":len(imprecise),"risk_flags":timing_risks,"historical_timestamps":"mixed_quality",
                    "observation_limits":{'weather':'not_continuously_measured','european_storage':'not_continuously_measured','actual_gas_flow':'not_continuously_measured','lng_arrivals':'not_continuously_measured'}},"analysis_kind":kind}


def update_event_memory(store, news: dict):
    text = str(news.get("title", ""))+" "+str(news.get("summary", ""))+" "+str(news.get('fact_excerpt') or '')
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
        "known_at":news.get("version_seen_at",news["first_seen_at"]),"state":state,"title":news.get("title"),"summary":news.get("summary"),"fact_excerpt":news.get('fact_excerpt'),
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
