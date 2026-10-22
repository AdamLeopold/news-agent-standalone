import asyncio
from datetime import date, timedelta
import logging
import time
from .calendar import iso, utcnow, cutoff, trading_day, parse_time
from .config import POLL_SECONDS
from .context import build_context, update_event_memory

log = logging.getLogger(__name__)


def market_refresh_reason(latest: dict, old: dict, baseline: dict, now=None):
    """Bound retries and compare prices only within one contract identity."""
    if not latest or latest.get('status') in ('queued','running') or not baseline.get('available'):
        return None
    now=now or utcnow()
    if (now-parse_time(latest['created_at'])).total_seconds()<900:
        return None
    if not old.get('available'):
        return 'first_price_baseline'
    if baseline.get('contract')!=old.get('contract'):
        return 'contract_change'
    # A corrected daily reference can reverse the observed move even when the
    # latest quote is unchanged. Compare its identity and threshold separately
    # after the contract check, without subtracting prices across contracts.
    if baseline.get('previous_date')!=old.get('previous_date'):
        return 'previous_reference_date_change'
    import math
    def same_number(left, right):
        if left is None or right is None:
            return left is right
        try:
            return math.isclose(float(left),float(right),rel_tol=0,abs_tol=1e-9)
        except (TypeError,ValueError):
            return left==right
    if not same_number(baseline.get('previous_price'),old.get('previous_price')):
        return 'previous_reference_price_change'
    if not same_number(baseline.get('extreme_threshold'),old.get('extreme_threshold')):
        return 'extreme_threshold_change'
    if abs(baseline['latest_price']-old['latest_price'])>=.30-1e-9:
        return 'market_price_change'
    return None


def freeze_due(store, now=None):
    now = now or utcnow()
    with store.connection() as c:
        days = {r[0] for r in c.execute("SELECT DISTINCT trade_date FROM analyses WHERE kind='live'")}
    today = now.astimezone(__import__('zoneinfo').ZoneInfo('Europe/Amsterdam')).date()
    if trading_day(today):
        days.add(today.isoformat())
    for ds in sorted(days):
        day = date.fromisoformat(ds)
        end = cutoff(day)
        if end > now:
            continue
        end_iso = iso(end)
        with store.connection() as c:
            if c.execute("SELECT 1 FROM daily_snapshots WHERE trade_date=?", (ds,)).fetchone():
                continue
            rows = c.execute("SELECT id FROM analyses WHERE trade_date=? AND kind='live' AND status='completed' AND completed_at<? AND created_at<? ORDER BY created_at DESC,completed_at DESC", (ds,end_iso,end_iso)).fetchall()
            # A review already known at the close prevents freezing an invalid
            # signal. Later reviews cannot rewrite what was known at that time.
            aid = next((row[0] for row in rows if 'brief' not in
                        ((store.analysis_review(row[0], as_of=end_iso) or {}).get('invalid_parts') or [])), None)
            c.execute("INSERT INTO daily_snapshots VALUES (?,?,?,?)", (ds,end_iso,aid,iso(now)))
            if aid:
                c.execute("UPDATE analyses SET frozen=1 WHERE id=?", (aid,))
        store.emit("daily_frozen", {"trade_date":ds,"analysis_id":aid})


def _inference_metric(store, kind, data):
    """Telemetry failures must never change a persisted model result."""
    try:
        store.metric(kind,data)
    except Exception:
        log.exception("Could not persist inference metric %s; job state preserved",kind)


def _job_result_committed(store, job):
    if job['kind']=='translation':
        record=store.translation_record(job['analysis_id'])
        return bool(record and record['status']=='completed' and record.get('result') is not None)
    record=store.analysis(job['analysis_id'])
    prefix='detail_' if job['kind']=='detail' else ''
    return bool(record and record[prefix+'status']=='completed' and record.get(prefix+'result') is not None)


async def inference_worker(store, analyst):
    while True:
        job = store.claim_job()
        if not job:
            await asyncio.sleep(1)
            continue
        started = time.monotonic()
        generated=False
        try:
            if job['kind']=='translation':
                from .translation import translate_analysis, source_hash
                translated=store.translation_record(job['analysis_id'])
                record=store.analysis(translated['analysis_id'])
                original=record['detail_result' if translated['part']=='detail' else 'result']
                if source_hash(original)!=translated['source_hash']:
                    raise ValueError('Source analysis changed; request a new English version')
                result=await translate_analysis(analyst,original)
            else:
                record = store.analysis(job["analysis_id"],include_input=True)
                if (job['kind']=='detail' and (record.get('result') or {}).get('_meta',{}).get('structured')
                        and hasattr(analyst,'detail_from_brief')):
                    result = await analyst.detail_from_brief(record['result'])
                else:
                    result = await analyst.analyze(record["input_snapshot"],detail=job["kind"]=="detail")
            generated=True
            store.finish_job(job,result=result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message = str(exc)[:1800]
            committed=False
            if generated:
                try:
                    # finish_job commits before emitting SSE. A notification
                    # failure is not permission to erase its committed result.
                    committed=_job_result_committed(store,job)
                except Exception:
                    log.exception("Cannot verify job commit; leave persisted state for recovery")
                    continue
            if committed:
                log.exception("Post-commit job notification failed; completed result preserved")
                _inference_metric(store,'post_commit_notification_failure',{'analysis_id':job['analysis_id'],'kind':job['kind'],'error':message})
            else:
                store.finish_job(job,error=message)
                from .prompts import PROMPT_VERSION
                failure_meta={"prompt_version":getattr(analyst,'prompt_version',PROMPT_VERSION),"reasoning":getattr(analyst,'reasoning',None),
                              **getattr(exc,'audit',{})}
                _inference_metric(store,"translation_failure" if job['kind']=='translation' else "model_failure",{"analysis_id":job["analysis_id"],"kind":job["kind"],"seconds":round(time.monotonic()-started,3),"error":message,"model_metadata":failure_meta})
                log.exception("Model job failed")
                continue
        _inference_metric(store,"translation_success" if job['kind']=='translation' else "model_success",{"analysis_id":record["id"],"kind":job["kind"],"seconds":round(time.monotonic()-started,3),"model_metadata":result.get("_meta",{})})
        if job['kind']=='short' and record['kind']=='live':
            try:
                store.request_translation(record['id'])
            except Exception as exc:
                log.exception("English enqueue failed; original model result preserved")
                _inference_metric(store,'translation_enqueue_failure',{'analysis_id':record['id'],'error':str(exc)[:300]})


async def collection_worker(store):
    from .ingest import poll_sources
    while True:
        started = time.monotonic()
        try:
            result = await poll_sources(store)
            created = result.get("created", []) + result.get("revised", [])
            from .calendar import session_at
            current_session=session_at()
            current = store.window_news(current_session["trade_date"],iso())+store.observed_imprecise_news(current_session['window_start'],iso())
            # Cold-start or wake recovery: previously backfilled current-window records still need a brief.
            candidates = created + [n for n in current if store.latest_analysis(news_id=n["id"]) is None]
            seen = set()
            for news in candidates:
                if news["id"] in seen:
                    continue
                seen.add(news["id"])
                update_event_memory(store,news)
                published = news.get("published_at")
                if not published:
                    if news.get('ingestion_kind')!='live':
                        continue
                    published=news.get('first_seen_at')
                revision = news.get('updated_at')
                if revision:
                    try:
                        published=max(parse_time(published),parse_time(revision))
                    except (ValueError,TypeError):
                        pass
                age = (utcnow()-parse_time(published)).total_seconds()
                if -60 <= age <= 48*3600:
                    context = build_context(store,news)
                    delta = (context.get('current_news') or {}).get('revision_delta') or {}
                    if news.get('_revised') and delta.get('substantive_change') is False:
                        previous = store.previous_news_version(news['id'], news.get('version', 1), context['as_of'])
                        # Keep genuine publication/window corrections. A new
                        # retrieval clock alone is not a new price-analysis event.
                        if previous and previous['data'].get('trade_date') == news.get('trade_date'):
                            continue
                    kind = "live" if news.get("trade_date")==context["session"]["trade_date"] or news.get('ingestion_kind')=='live' and (news.get('_revised') or not news.get('published_at')) else "article"
                    context["analysis_kind"] = kind
                    store.create_analysis(context,kind=kind,news_id=news["id"],priority=10 if kind=='live' else 20)
            # Carry prior known facts forward without relabelling them as today's headlines.
            session = session_at()
            if store.latest_analysis(trade_date=session['trade_date'],live_only=True) is None:
                recent = [n for n in store.news(100,scope='recent') if n.get('published_at') and parse_time(n['published_at'])<=utcnow()]
                trigger = max(recent,key=lambda n:n['published_at']) if recent else None
                context = build_context(store,trigger)
                if context['window_news'] or context['historical_events'] or trigger:
                    context['outlook_trigger']='startup_or_new_window'
                    if not context['window_news']:
                        context['coverage']['risk_flags']=list(dict.fromkeys(context['coverage'].get('risk_flags',[])+['本结算窗口暂无新增新闻，此预判依据近五日新闻与持续事件，未把旧新闻归为今日新增。']))
                    store.create_analysis(context,kind='live',priority=3)
            else:
                # New prices can change the close baseline without any new
                # headline. Refresh at most every 15 minutes after a material
                # same-contract move, never crowding pending live analyses.
                latest=store.latest_analysis(trade_date=session['trade_date'],live_only=True)
                if latest and latest['status'] not in ('queued','running'):
                    now=iso()
                    completed=store.latest_analysis(trade_date=session['trade_date'],live_only=True,completed_only=True)
                    record=store.analysis((completed or latest)['id'],include_input=True)
                    from .context import price_context
                    reference=price_context(store,now)
                    baseline=reference.get('market_baseline') or {}
                    old=((record.get('input_snapshot') or {}).get('price_reference') or {}).get('market_baseline') or {}
                    refresh_reason=market_refresh_reason(latest,old,baseline)
                    if refresh_reason:
                        focus=(record.get('input_snapshot') or {}).get('current_news')
                        context=build_context(store,focus,as_of=now)
                        context['outlook_trigger']='market_baseline_update'
                        context['outlook_refresh_reason']=refresh_reason
                        store.create_analysis(context,kind='live',priority=12)
            store.metric("collection_cycle",{"seconds":round(time.monotonic()-started,3),"created":len(result.get("created", [])),"revised":len(result.get("revised", []))})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            store.metric("collection_failure",{"error":str(exc)[:1000]})
            log.exception("Collection cycle failed")
        await asyncio.sleep(max(5,POLL_SECONDS-(time.monotonic()-started)))


async def maintenance_worker(store):
    from .prices import refresh_prices
    next_prices = 0
    while True:
        try:
            freeze_due(store)
            for event in store.events(10000):
                if not event.get('event_start') or event.get('state')=='resolved':
                    continue
                now=utcnow()
                expected='resolved' if event.get('event_end') and parse_time(event['event_end'])<=now else ('scheduled' if parse_time(event['event_start'])>now else 'active')
                if event.get('state')!=expected:
                    event['state']=expected
                    if expected=='resolved' and event.get('source')=='gassco' and str(event.get('event_status','')).lower()=='active':
                        event['resolution_basis']='announced_window_elapsed_unverified_flow'
                        event['risk_flags']=list(dict.fromkeys(event.get('risk_flags',[])+['公告计划时段已结束，实际流量恢复待核实']))
                    store.upsert_event(event)
            if time.monotonic() >= next_prices:
                with store.connection() as c:
                    last_price=c.execute('SELECT MAX(day) FROM prices').fetchone()[0]
                price_start=(date.fromisoformat(last_price)-timedelta(days=7)).isoformat() if last_price else '2024-02-01'
                await refresh_prices(store,start_date=price_start)
                from .evaluation import evaluate_frozen, enrich_outcomes
                # Historical outcome queries can take minutes on the first run.
                # Keep health, SSE, collection and inference responsive while they finish.
                await asyncio.to_thread(evaluate_frozen,store)
                await asyncio.to_thread(enrich_outcomes,store)
                next_prices = time.monotonic()+3600
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            store.metric("maintenance_failure",{"error":str(exc)[:1000]})
            log.exception("Maintenance failed")
            next_prices = time.monotonic()+300
        await asyncio.sleep(20)

