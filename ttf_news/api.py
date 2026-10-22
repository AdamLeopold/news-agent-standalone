import asyncio
from contextlib import asynccontextmanager
import csv
from datetime import datetime
import io
import json
import os
from pathlib import Path
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .config import ROOT, MODEL, OLLAMA_URL, PORT
from .calendar import iso, parse_time, session_at
from .store import Store, dumps
from .context import build_context, aggregate, price_context, update_event_memory
from .legacy import import_legacy
from .evaluation import monitoring_report


class ManualInput(BaseModel):
    text: str = Field(min_length=15,max_length=30000)
    title: str | None = Field(default=None,max_length=500)
    source: str | None = Field(default=None,max_length=256)
    published_at: str | None = None

    @field_validator("published_at")
    @classmethod
    def valid_time(cls,value):
        return iso(parse_time(value)) if value else None


def create_app(store: Store | None = None, analyst=None, workers: bool = True) -> FastAPI:
    store = store or Store()
    inference_enabled=workers and os.environ.get('TTF_NEWS_INFER','1')!='0'
    if analyst is None:
        from .structured_analysis import StructuredNewsAnalyst
        analyst = StructuredNewsAnalyst(model=MODEL,base_url=OLLAMA_URL)

    @asynccontextmanager
    async def lifespan(app):
        import_legacy(store)
        store.recover_jobs()
        store.metric("service_start",{"model":MODEL})
        tasks = []
        if workers:
            from .runtime import inference_worker,collection_worker,maintenance_worker
            from .market import market_worker
            if inference_enabled:
                tasks.append(asyncio.create_task(inference_worker(store,analyst)))
            tasks.append(asyncio.create_task(market_worker(store)))
            if os.environ.get("TTF_NEWS_COLLECT","1") != "0":
                tasks.append(asyncio.create_task(collection_worker(store)))
                tasks.append(asyncio.create_task(maintenance_worker(store)))
            tasks.append(asyncio.create_task(heartbeat()))
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            store.metric("service_stop",{})

    async def heartbeat():
        from .runtime import freeze_due
        while True:
            freeze_due(store)
            store.metric("heartbeat",{"queued_jobs":store.counts()["analyses"]})
            await asyncio.sleep(60)

    app = FastAPI(title="TTF 新闻分析工作台",version="0.1.0",lifespan=lifespan)
    app.state.store = store
    app.state.analyst = analyst

    @app.middleware("http")
    async def local_origin(request: Request,call_next):
        origin = request.headers.get("origin")
        if request.method not in ("GET","HEAD","OPTIONS") and origin and origin not in (f"http://127.0.0.1:{PORT}",f"http://localhost:{PORT}","http://127.0.0.1:5173","http://localhost:5173"):
            return JSONResponse({"detail":"Only the local application may submit requests"},status_code=403)
        return await call_next(request)

    @app.get("/api/health")
    async def health():
        from .prompts import PROMPT_VERSION
        model = await analyst.health()
        return {"status":"ok" if model.get("available") else "model_unavailable", "server_pid":os.getpid(),"prompt_version":getattr(analyst,'prompt_version',PROMPT_VERSION),"model":model,"counts":store.counts(),"sources":store.sources(),"collector":{"enabled":os.environ.get("TTF_NEWS_COLLECT","1")!="0","poll_seconds":int(os.environ.get("TTF_NEWS_POLL_SECONDS","60"))},'inference':{'enabled':inference_enabled},"server_time":iso()}

    @app.get("/api/dashboard")
    def dashboard():
        now = iso()
        session = session_at(now)
        articles = store.window_news(session["trade_date"],now)
        events = store.retrieve_events(" ".join(str(n.get("title","")) for n in articles),now,
                                      exclude_news_ids=[n["id"] for n in articles])
        newest=store.latest_analysis(trade_date=session['trade_date'],completed_only=True,live_only=True)
        latest=store.latest_analysis(trade_date=session['trade_date'],completed_only=True,live_only=True,exclude_invalid=True)
        excluded=({'id':newest['id'],'completed_at':newest.get('completed_at'),'review':newest.get('review')}
                  if newest and newest['id']!=(latest or {}).get('id') else None)
        return {"session":session,"daily":aggregate(articles),"latest_analysis":latest,'excluded_latest_analysis':excluded,
            "important_events":events,"coverage":{"counts":store.counts(),"sources":store.sources()},"price_reference":price_context(store,now),"monitoring":monitoring_report(store)}

    @app.get('/api/market')
    def market_data():
        from .market import market_view
        return market_view(store)

    def news_with_analysis(n):
        analysis = store.latest_analysis(news_id=n["id"])
        return {**n,"analysis_id":analysis["id"] if analysis else None,"analysis_status":analysis["status"] if analysis else "not_requested","analysis_created_at":analysis.get("created_at") if analysis else None,"analysis_completed_at":analysis.get("completed_at") if analysis else None,"analysis":analysis.get("result") if analysis else None,'review':analysis.get('review') if analysis else None}

    @app.get("/api/news")
    def news_list(limit:int=50,offset:int=0,trade_date:str|None=None,scope:str="recent"):
        limit = min(max(limit,1),200)
        if scope not in ("all","live","recent"):
            raise HTTPException(422,"Invalid news scope")
        return {"items":[news_with_analysis(n) for n in store.news(limit,max(offset,0),trade_date,scope)],"total":store.counts()["news"],"scope":scope}

    @app.get("/api/news/{identifier}")
    def news_detail(identifier:str):
        news = store.get_news(identifier)
        if not news:
            raise HTTPException(404,"News not found")
        return news_with_analysis(news)

    @app.post("/api/news/{identifier}/analyze")
    def analyze_news(identifier:str):
        news = store.get_news(identifier)
        if not news:
            raise HTTPException(404,"News not found")
        context = build_context(store,news)
        kind = "live" if any(n['id']==identifier for n in context['window_news']) else "article"
        context["analysis_kind"] = kind
        return store.create_analysis(context,kind=kind,news_id=identifier,priority=5 if kind=='live' else 20)

    @app.post("/api/manual/analyze",status_code=202)
    def manual(payload:ManualInput):
        news = {"id":"manual-"+str(uuid.uuid4()),"title":payload.title or payload.text[:100],"summary":payload.text,"source":payload.source or "手动输入",
            "url":None,"published_at":payload.published_at,"first_seen_at":iso(),"timestamp_precision":"exact" if payload.published_at else "unknown","timestamp_source":"user_supplied" if payload.published_at else "manual_observed_now","version":1}
        return store.create_analysis(build_context(store,news,kind="manual"),kind="manual",priority=0)

    @app.get("/api/analyses/{identifier}")
    def analysis_record(identifier:str,include_input:bool=False):
        record = store.analysis(identifier,include_input=include_input)
        if not record:
            raise HTTPException(404,"Analysis not found")
        return record

    @app.post("/api/analyses/{identifier}/detail",status_code=202)
    def analysis_detail(identifier:str):
        try:
            return store.request_detail(identifier)
        except KeyError:
            raise HTTPException(404,"Analysis not found")
        except ValueError as exc:
            raise HTTPException(409,str(exc))

    @app.post('/api/analyses/{identifier}/translation',status_code=202)
    def translate_record(identifier: str, part: str='brief', retry: bool=False):
        try:
            return store.request_translation(identifier,part=part,retry=retry)
        except KeyError:
            raise HTTPException(404,'Analysis not found')
        except ValueError as exc:
            raise HTTPException(409,str(exc))

    @app.get('/api/analyses/{identifier}/translation')
    def cached_translation(identifier: str, part: str='brief'):
        try:
            return store.cached_translation(identifier, part) or {'status': 'not_requested'}
        except KeyError:
            raise HTTPException(404, 'Analysis not found')
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @app.get('/api/translations/{identifier}')
    def translation_status(identifier: str):
        data=store.translation_record(identifier)
        if not data:
            raise HTTPException(404,'Translation not found')
        return data

    @app.get("/api/events")
    def events(limit:int=100):
        return {"items":store.events(min(max(limit,1),5000)),"total":store.counts()["events"]}

    @app.get("/api/coverage")
    def coverage():
        return store.coverage()

    @app.get("/api/monitoring")
    def monitoring():
        return monitoring_report(store)

    @app.get("/api/daily/{day}")
    def daily(day:str):
        from .calendar import session_for_day
        try:
            session = session_for_day(day)
        except ValueError as exc:
            raise HTTPException(422,str(exc))
        with store.connection() as c:
            snapshot = c.execute("SELECT * FROM daily_snapshots WHERE trade_date=?",(day,)).fetchone()
        return {"session":session,"daily":aggregate(store.window_news(day,iso(),historical=True)),"frozen_prediction":dict(snapshot) if snapshot else None,"historical_view":True,"note":"此汇总含事后回补；不代表历史时刻已可获得。"}

    @app.get("/api/stream")
    async def stream(request:Request):
        raw_cursor=request.headers.get("last-event-id",request.query_params.get("after"))
        try:
            if raw_cursor is None:
                with store.connection() as c:
                    cursor=c.execute('SELECT COALESCE(MAX(id),0) FROM stream_events').fetchone()[0]
            else:
                cursor=max(0,int(raw_cursor))
        except ValueError:
            raise HTTPException(422,"Invalid event cursor")
        async def generate():
            nonlocal cursor
            if raw_cursor is None:
                yield f"id: {cursor}\nevent: update\ndata: {{\"type\":\"stream_ready\"}}\n\n"
            while not await request.is_disconnected():
                for event in store.stream_since(cursor):
                    cursor = event["id"]
                    payload = {"type":event["type"],**json.loads(event["payload"])}
                    yield f"id: {cursor}\nevent: update\ndata: {dumps(payload)}\n\n"
                yield ": heartbeat\n\n"
                await asyncio.sleep(2)
        return StreamingResponse(generate(),media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

    @app.get("/api/export/{filename}")
    def export(filename:str):
        from .export import render_export
        try:
            content,media = render_export(store,filename)
        except ValueError as exc:
            raise HTTPException(404,str(exc))
        return Response(content,media_type=media,headers={"Content-Disposition":f'attachment; filename="{filename}"'})

    dist = ROOT / "frontend" / "dist"
    if (dist / "assets").exists():
        app.mount("/assets",StaticFiles(directory=dist/"assets"),name="assets")

    @app.get("/{path:path}")
    def frontend(path:str):
        if path.startswith("api/"):
            raise HTTPException(404,"API not found")
        index = dist / "index.html"
        if index.exists():
            return FileResponse(index)
        return Response("Frontend build pending. Run npm run build in frontend/.",media_type="text/plain",status_code=503)
    return app


app = create_app()
