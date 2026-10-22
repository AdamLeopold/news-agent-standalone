import json
from datetime import date
from .calendar import iso, previous_day,session_at
from .labels import actual_label
from .store import dumps


def evaluate_frozen(store):
    with store.connection() as c:
        snapshots = [dict(r) for r in c.execute("SELECT * FROM daily_snapshots WHERE analysis_id IS NOT NULL")]
    for snapshot in snapshots:
        analysis = store.analysis(snapshot["analysis_id"],include_input=True)
        ref = analysis["input_snapshot"].get("price_reference",{})
        contract = ref.get("contract")
        status = "awaiting_verified_settlement"
        delta = direction = correct = None
        t = ref.get("extreme_threshold")
        actual = None
        if contract:
            with store.connection() as c:
                row = c.execute("SELECT data FROM prices WHERE contract=? AND day=? AND price_type='settlement'", (contract,snapshot["trade_date"])).fetchone()
            actual = json.loads(row[0]) if row else None
        expected_previous = previous_day(date.fromisoformat(snapshot["trade_date"])).isoformat()
        if ref.get("price_date") != expected_previous:
            status = "missing_previous_trading_day_settlement"
        elif actual and actual.get("verified") and ref.get("verified") and ref.get("previous_price") is not None:
            delta = actual["value"]-ref["previous_price"]
            direction = actual_label(delta,t)
            if direction:
                status = "evaluated"
                correct = int(direction==analysis["result"]["close_forecast"]["direction"])
            else:
                status = "insufficient_volatility_history"
        data = {"price_source":ref.get("reference_source"),"reason":"仅已验证的同合约日结算参与正式评分", "evaluation_updated_at":iso()}
        if analysis['result']['close_forecast'].get('direction') is None:
            status='insufficient_forecast_evidence'
            correct=None
        with store.connection() as c:
            c.execute("INSERT OR REPLACE INTO evaluations VALUES (?,?,?,?,?,?,?,?,?,?)", (snapshot["trade_date"],analysis["id"],contract,delta,t,direction,analysis["result"]["close_forecast"]["direction"],correct,status,dumps(data)))


def enrich_outcomes(store):
    now = iso()
    last_completed_day = previous_day(date.fromisoformat(session_at(now)["trade_date"])).isoformat()
    # Reference moves are explicitly descriptive, never event-causal labels.
    for event in store.events(10000):
        if event.get("source") == "legacy_daily":
            continue
        if all(f'{h}d' in event.get('outcomes',{}) for h in (1,3,5,10)):
            continue
        day = event.get("event_date")
        if not day:
            continue
        from .context import price_context
        # Select the nearest eligible monthly contract at the event date; far-dated contracts
        # must not become the default historical reaction merely because they were inserted first.
        event_session=session_at(day+'T00:00:00+00:00')
        # Backfilled prices may be useful for a retrospective outcome now,
        # without pretending the local system knew them on the event date.
        reference=price_context(store,now,reference_trade_date=event_session['trade_date'])
        if not reference.get('contract'):
            continue
        with store.connection() as c:
            rows = [json.loads(r[0]) for r in c.execute('''SELECT v.data FROM price_versions v JOIN
              (SELECT day,MAX(known_at) latest FROM price_versions
               WHERE contract=? AND price_type=? AND day>=? AND day<=? AND known_at<=? GROUP BY day) k
              ON v.day=k.day AND v.known_at=k.latest
              WHERE v.contract=? AND v.price_type=? ORDER BY v.day LIMIT 11''',
              (reference['contract'],reference['price_type'],day,last_completed_day,now,reference['contract'],reference['price_type']))]
        if len(rows)<2:
            continue
        outcomes = dict(event.get("outcomes",{}))
        changed = False
        for horizon in (1,3,5,10):
            key = f"{horizon}d"
            if key in outcomes or len(rows)<=horizon:
                continue
            outcomes[key] = {"change":rows[horizon]["value"]-rows[0]["value"],"contract":reference['contract'],"price_type":reference['price_type'],"base_date":rows[0]["day"],"end_date":rows[horizon]["day"],"available_at":now,"interpretation":"同期价格变化，非因果效应"}
            changed = True
        if changed:
            event.update(outcomes=outcomes,outcome_available_at=now)
            store.upsert_event(event)


def monitoring_report(store) -> dict:
    with store.connection() as c:
        metrics = [dict(r) for r in c.execute("SELECT * FROM metrics ORDER BY id")]
        evaluations = [dict(r) for r in c.execute("SELECT * FROM evaluations ORDER BY trade_date")]
    for m in metrics:
        m["data"] = json.loads(m["data"])
    baselines=[m['created_at'] for m in metrics if m['type']=='validation_started']
    if baselines:
        metrics=[m for m in metrics if m['created_at']>=baselines[-1]]
    successes = [m for m in metrics if m["type"]=="model_success"]
    failures = [m for m in metrics if m["type"]=="model_failure"]
    started = metrics[0]["created_at"] if metrics else None
    from .calendar import parse_time,utcnow
    elapsed = (utcnow()-parse_time(started)).total_seconds()/86400 if started else 0
    # Elapsed time is not proof of uninterrupted uptime. Gaps in heartbeats are separately measured.
    beats = [parse_time(m["created_at"]) for m in metrics if m["type"]=="heartbeat"]
    gaps = [round((b-a).total_seconds()) for a,b in zip(beats,beats[1:]) if (b-a).total_seconds()>180]
    tail_gap = round((utcnow()-beats[-1]).total_seconds()) if beats else None
    first_gap = round((beats[0]-parse_time(started)).total_seconds()) if beats and started else None
    if tail_gap and tail_gap>180:
        gaps.append(tail_gap)
    if first_gap and first_gap>180:
        gaps.insert(0,first_gap)
    def latency_summary(rows):
        values=sorted(m['data']['seconds'] for m in rows if 'seconds' in m['data'])
        if not values:
            return {"count":0,"median":None,"p95":None}
        import statistics, math
        return {"count":len(values),"median":round(statistics.median(values),3),"p95":values[max(0,math.ceil(len(values)*.95)-1)]}
    revisions={}
    for entry in successes+failures:
        version=entry['data'].get('model_metadata',{}).get('prompt_version') or 'unversioned'
        group=revisions.setdefault(version,{'successes':0,'failures':0,'first_at':entry['created_at'],'last_at':entry['created_at']})
        group['successes' if entry['type']=='model_success' else 'failures']+=1
        group['first_at']=min(group['first_at'],entry['created_at'])
        group['last_at']=max(group['last_at'],entry['created_at'])
    for version,group in revisions.items():
        selected=[s for s in successes if (s['data'].get('model_metadata',{}).get('prompt_version') or 'unversioned')==version]
        group['brief_latency']=latency_summary([s for s in selected if s['data'].get('kind')!='detail'])
        group['detail_latency']=latency_summary([s for s in selected if s['data'].get('kind')=='detail'])
    from .score_report import classification_report
    return {"started_at":started,"elapsed_days":round(elapsed,4),"required_days":7,"seven_day_validation_complete":elapsed>=7 and bool(beats) and not gaps,
        "heartbeat_gap_seconds":gaps,"model_successes":len(successes),"model_failures":len(failures),
        "last_heartbeat_at":iso(beats[-1]) if beats else None,"seconds_since_heartbeat":tail_gap,
        "model_failure_rate":round(len(failures)/(len(successes)+len(failures)),4) if successes or failures else None,
        "brief_latency":latency_summary([m for m in successes if m['data'].get('kind')!='detail']),
        "detail_latency":latency_summary([m for m in successes if m['data'].get('kind')=='detail']),
        "collection_cycles":sum(m['type']=='collection_cycle' for m in metrics),
        "collection_failures":sum(m['type']=='collection_failure' for m in metrics),
        "evaluation_report":classification_report(evaluations),
        "latency_seconds":[s["data"]["seconds"] for s in successes],"evaluations":evaluations,
        "official_evaluation_count":sum(e["status"]=="evaluated" for e in evaluations),"model_revisions":revisions,
        "notes":["七天持续运行验收需要真实经过七天并核查心跳缺口。","无已验证日结算时不报告正式预测准确率。"]}
