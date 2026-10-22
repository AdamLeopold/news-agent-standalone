"""In-memory ASGI/HTTPX regressions; no sockets, DB, model or service changes."""
import asyncio,importlib.util,os
from pathlib import Path
import anyio,httpx,pytest
from starlette.requests import Request
ROOT=Path(__file__).resolve().parents[2]

@pytest.fixture
def anyio_backend():return 'asyncio'

@pytest.fixture
def make_gateway():
 from ttf_news.public_gateway import create_gateway
 return lambda handler:create_gateway(transport=httpx.MockTransport(handler),queue_check=lambda:True)

def request_scope():
 return {'type':'http','asgi':{'version':'3.0','spec_version':'2.4'},'method':'GET','scheme':'http','path':'/api/stream','raw_path':b'/api/stream','root_path':'','query_string':b'after=41','headers':[(b'origin',b'https://adamleopold.github.io'),(b'last-event-id',b'41')],'server':('local',80),'client':('local',1)}
async def receive():return {'type':'http.request','body':b'','more_body':False}
async def forward(app):
 route=next(r for r in app.routes if getattr(r,'path',None)=='/{path:path}')
 return await route.endpoint(Request(request_scope(),receive),path='api/stream')
async def consume(response):
 return b''.join([chunk async for chunk in response.body_iterator])

class Bytes(httpx.AsyncByteStream):
 def __init__(self,*,fail=False,wait=False):self.fail=fail;self.wait=wait;self.close_calls=0;self.close_completed=False;self.read_started=anyio.Event()
 async def __aiter__(self):
  self.read_started.set()
  if self.fail:raise httpx.ReadError('bounded mock upstream body failure')
  yield b'id: 42\nevent: update\ndata: {}\n\n'
  if self.wait:await anyio.sleep_forever()
 async def aclose(self):
  self.close_calls+=1
  await anyio.sleep(0.001)
  self.close_completed=True

@pytest.mark.anyio
async def test_sixteen_body_errors_release_slots_for_seventeenth(make_gateway):
 streams=[]
 def handler(req):
  stream=Bytes(fail=len(streams)<16);streams.append(stream);return httpx.Response(200,stream=stream,headers={'content-type':'text/event-stream'})
 app=make_gateway(handler)
 async with app.router.lifespan_context(app):
  for _ in range(16):
   response=await forward(app);assert response.status_code==200
   with pytest.raises(httpx.ReadError):await consume(response)
  response=await forward(app);assert response.status_code==200
  assert b'id: 42' in await consume(response);await response.background()
  assert all(s.close_completed and s.close_calls==1 for s in streams)

@pytest.mark.anyio
async def test_sixteen_cancelled_setups_release_slots_for_seventeenth(make_gateway):
 starts=[];healthy=False
 async def handler(req):
  if healthy:return httpx.Response(200,stream=Bytes())
  started=anyio.Event();starts.append(started);started.set();await anyio.sleep_forever()
 app=make_gateway(handler)
 async with app.router.lifespan_context(app):
  for index in range(16):
   task=asyncio.create_task(forward(app))
   while len(starts)<=index:await anyio.sleep(0)
   await starts[index].wait();task.cancel()
   with pytest.raises(asyncio.CancelledError):await task
  healthy=True;response=await forward(app);assert response.status_code==200
  await consume(response);await response.background()

@pytest.mark.anyio
async def test_sixteen_iterator_cancellations_release_slots(make_gateway):
 streams=[]
 def handler(req):
  stream=Bytes(wait=True);streams.append(stream);return httpx.Response(200,stream=stream)
 app=make_gateway(handler)
 async with app.router.lifespan_context(app):
  for _ in range(16):
   response=await forward(app);assert response.status_code==200
   task=asyncio.create_task(consume(response));await streams[-1].read_started.wait();await anyio.sleep(0);task.cancel()
   with pytest.raises(asyncio.CancelledError):await task
  response=await forward(app);assert response.status_code==200;await response.background()
  assert all(s.close_completed and s.close_calls==1 for s in streams)

@pytest.mark.anyio
async def test_asgi_send_cancellation_closes_suspended_iterator_immediately(make_gateway):
 streams=[]
 def handler(req):
  stream=Bytes(wait=True);streams.append(stream);return httpx.Response(200,stream=stream)
 async def cancelled_send(message):
  if message['type']=='http.response.body':raise asyncio.CancelledError()
 app=make_gateway(handler)
 async with app.router.lifespan_context(app):
  for _ in range(16):
   response=await forward(app);assert response.status_code==200
   with pytest.raises(asyncio.CancelledError):await response(request_scope(),receive,cancelled_send)
   assert streams[-1].close_completed
  response=await forward(app);assert response.status_code==200;await response.background()

@pytest.mark.anyio
async def test_true_sixteen_active_cap_and_background_idempotence(make_gateway):
 streams=[]
 def handler(req):
  stream=Bytes(wait=True);streams.append(stream);return httpx.Response(200,stream=stream)
 app=make_gateway(handler)
 async with app.router.lifespan_context(app):
  active=[await forward(app) for _ in range(16)];assert all(r.status_code==200 for r in active)
  assert (await forward(app)).status_code==429
  await active[0].background();await active[0].background()
  replacement=await forward(app);assert replacement.status_code==200
  assert (await forward(app)).status_code==429
  for r in active[1:]+[replacement]:await r.background()
  assert all(s.close_calls==1 and s.close_completed for s in streams)

@pytest.mark.anyio
async def test_cancel_scope_does_not_interrupt_upstream_aclose(make_gateway):
 stream=Bytes(wait=True);app=make_gateway(lambda req:httpx.Response(200,stream=stream))
 async with app.router.lifespan_context(app):
  response=await forward(app)
  with anyio.CancelScope() as scope:
   scope.cancel();await response.background()
  assert stream.close_completed and stream.close_calls==1
  await response.background();assert stream.close_calls==1

@pytest.mark.anyio
async def test_successful_stream_cursor_origin_headers_still_forwarded(make_gateway):
 requests=[];stream=Bytes()
 def handler(req):requests.append(req);return httpx.Response(200,stream=stream,headers={'content-type':'text/event-stream','cache-control':'no-cache'})
 app=make_gateway(handler)
 async with app.router.lifespan_context(app):
  response=await forward(app);assert response.status_code==200
  assert response.headers['content-type']=='text/event-stream'
  assert response.headers['x-content-type-options']=='nosniff'
  assert b'id: 42' in await consume(response)
  await response.background()
  assert requests[0].headers['last-event-id']=='41' and requests[0].url.params['after']=='41'
  assert stream.close_completed and stream.close_calls==1
