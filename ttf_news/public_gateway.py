"""Public website adapter; the existing collector/model process stays unchanged.

Only application API routes are forwarded. This is not a general HTTP or Ollama
proxy. Cross-origin submissions and bounded model queues are handled here so
the seven-day local service does not need a restart for Pages deployment.
"""
import asyncio
import anyio
from collections import deque
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import re
import sqlite3
import time

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

ROOT = Path(__file__).resolve().parents[1]
# The original form accepts 30,000 Unicode characters. UTF-8 and JSON escapes
# can use several bytes per character; keep that form contract through the proxy.
MAX_BODY = 256_000
ID = r"[A-Za-z0-9_.-]{1,150}"
READ_ROUTES = re.compile(
    rf"/api/(?:health|dashboard|news(?:/{ID})?|analyses/{ID}(?:/translation)?|translations/{ID}|market|events|coverage|monitoring|"
    r"daily/\d{4}-\d{2}-\d{2}|stream|export/(?:news|daily|events|prices)\.(?:csv|jsonl)|"
    r"export/(?:legacy_daily\.jsonl|analyses\.jsonl|coverage\.json))\Z"
)
WRITE_ROUTES = re.compile(rf"/api/(?:manual/analyze|news/{ID}/analyze|analyses/{ID}/(?:detail|translation))\Z")


def queue_available():
    from .config import DB_PATH
    try:
        with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=2) as c:
            return c.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0] < 12
    except sqlite3.Error:
        return False


def create_gateway(*, transport=None, queue_check=queue_available, allowed_origin=None):
    config = json.loads((ROOT / 'deployment/public.json').read_text())
    upstream = os.environ.get('TTF_NEWS_PUBLIC_UPSTREAM', config['upstream']).rstrip('/')
    origin = allowed_origin or config['allowed_origin']
    public_origin = config['api_base']
    allowed = [origin, public_origin]
    writes = deque()
    streams = 0
    cache = {}

    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(base_url=upstream, transport=transport,
                                     timeout=httpx.Timeout(60, read=90),
                                     limits=httpx.Limits(max_connections=32)) as client:
            app.state.client = client
            yield

    app = FastAPI(title='TTF public gateway', docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=allowed,
                       allow_methods=['GET', 'HEAD', 'POST', 'OPTIONS'],
                       allow_headers=['Content-Type', 'Last-Event-ID'],
                       expose_headers=['Content-Disposition'], max_age=600)

    @app.api_route('/{path:path}', methods=['GET', 'HEAD', 'POST'])
    async def forward(request: Request, path: str):
        nonlocal streams
        route = '/' + path
        write = request.method == 'POST'
        if not (WRITE_ROUTES if write else READ_ROUTES).fullmatch(route):
            return JSONResponse({'detail': 'Application route not found'}, status_code=404)
        supplied_origin = request.headers.get('origin')
        if supplied_origin and supplied_origin not in allowed:
            return JSONResponse({'detail': 'Origin not allowed'}, status_code=403)
        if write and supplied_origin not in allowed:
            return JSONResponse({'detail': 'Website origin required'}, status_code=403)
        body = bytearray()
        async for part in request.stream():
            body.extend(part)
            if len(body) > MAX_BODY:
                return JSONResponse({'detail': 'News input is too large'}, status_code=413)
        if write:
            # Global cap also bounds clients spoofing X-Forwarded-For/Origin.
            now = time.monotonic()
            while writes and writes[0] < now - 600:
                writes.popleft()
            if len(writes) >= 30 or not await asyncio.to_thread(queue_check):
                return JSONResponse({'detail': 'Model queue is busy; please retry shortly'},
                                    status_code=429, headers={'Retry-After': '60'})
            writes.append(now)
            cache.clear()
        is_stream = route == '/api/stream'
        if is_stream and streams >= 16:
            return JSONResponse({'detail': 'Stream capacity reached; polling remains available'}, status_code=429)
        ttl = {'/api/health': 10, '/api/dashboard': 3, '/api/news': 2, '/api/market': 5}.get(route, 0) if not write else 0
        key = str(request.url.query) + route
        cached = cache.get(key)
        if ttl and cached and time.monotonic() - cached[0] < ttl:
            return Response(cached[1], status_code=cached[2], headers=cached[3])
        headers = {'content-type': request.headers.get('content-type', 'application/json')}
        if write:
            headers['origin'] = upstream
        if request.headers.get('last-event-id'):
            headers['last-event-id'] = request.headers['last-event-id'][:32]
        if is_stream:
            streams += 1
        response = None
        released = False

        async def close_stream():
            nonlocal streams, released
            if not is_stream or released:
                return
            released = True
            # A disconnected request must still finish closing its upstream.
            with anyio.CancelScope(shield=True):
                try:
                    if response is not None:
                        await response.aclose()
                finally:
                    streams -= 1

        try:
            client = app.state.client
            req = client.build_request(request.method, route, params=request.query_params,
                                       content=bytes(body), headers=headers)
            response = await client.send(req, stream=True)
        except BaseException as exc:
            await close_stream()
            if isinstance(exc, httpx.HTTPError):
                return JSONResponse({'detail': 'Local TTF backend is temporarily unavailable'}, status_code=503)
            raise
        response_headers = {k: v for k, v in response.headers.items()
                            if k.lower() in ('content-type', 'content-disposition', 'cache-control', 'x-accel-buffering')}
        response_headers['X-Content-Type-Options'] = 'nosniff'
        if is_stream:
            async def stream_body():
                try:
                    async for part in response.aiter_bytes():
                        yield part
                finally:
                    await close_stream()

            class ClosingStreamingResponse(StreamingResponse):
                async def __call__(self, scope, receive, send):
                    # Cancellation may happen in ASGI send(), while the body
                    # iterator is suspended at yield rather than awaiting I/O.
                    try:
                        await super().__call__(scope, receive, send)
                    finally:
                        await close_stream()

            return ClosingStreamingResponse(stream_body(), status_code=response.status_code,
                                            headers=response_headers, background=BackgroundTask(close_stream))
        try:
            content = await response.aread()
        finally:
            await response.aclose()
        if ttl and response.status_code == 200:
            if len(cache) >= 64:
                cache.clear()
            cache[key] = (time.monotonic(), content, response.status_code, response_headers)
        return Response(content, status_code=response.status_code, headers=response_headers)

    return app


app = create_gateway()
