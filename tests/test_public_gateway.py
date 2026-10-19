import json
import httpx
import pytest
from fastapi.testclient import TestClient
from ttf_news.public_gateway import create_gateway, MAX_BODY

ORIGIN = 'https://adamleopold.github.io'


def fixture_gateway(*, available=True):
    calls = []
    def upstream(request):
        calls.append(request)
        if request.url.path == '/api/stream':
            return httpx.Response(200, content=b'id: 42\nevent: update\ndata: {"type":"news"}\n\n',
                                  headers={'content-type': 'text/event-stream'})
        return httpx.Response(202 if request.method == 'POST' else 200,
                              json={'ok': True, 'id': 'same-analysis'})
    app = create_gateway(transport=httpx.MockTransport(upstream), queue_check=lambda: available)
    return app, calls


def test_real_cross_origin_submission_preserves_payload_and_local_origin():
    app, calls = fixture_gateway()
    with TestClient(app) as client:
        r = client.post('/api/manual/analyze', json={'text': 'Norwegian gas supply interrupted.'},
                        headers={'Origin': ORIGIN})
    assert r.status_code == 202
    assert r.headers['access-control-allow-origin'] == ORIGIN
    assert calls[0].headers['origin'] == 'http://127.0.0.1:8765'
    assert b'Norwegian gas supply interrupted.' in calls[0].content


def test_pages_preflight_and_sse_cursor_are_preserved():
    app, calls = fixture_gateway()
    with TestClient(app) as client:
        r = client.options('/api/manual/analyze', headers={'Origin': ORIGIN,
                           'Access-Control-Request-Method': 'POST',
                           'Access-Control-Request-Headers': 'content-type'})
        assert r.status_code == 200
        r = client.get('/api/stream?after=41', headers={'Origin': ORIGIN, 'Last-Event-ID': '41'})
    assert r.headers['content-type'].startswith('text/event-stream')
    assert 'id: 42' in r.text
    assert calls[0].url.params['after'] == '41'
    assert calls[0].headers['last-event-id'] == '41'


def test_original_thirty_thousand_chinese_character_input_passes_gateway():
    app, calls = fixture_gateway()
    text = '气' * 30000
    with TestClient(app) as client:
        response = client.post('/api/manual/analyze', json={'text': text}, headers={'Origin': ORIGIN})
    assert response.status_code == 202
    assert json.loads(calls[0].content)['text'] == text


@pytest.mark.parametrize('path', ['/api/generate', '/api/tags', '/docs', '/openapi.json', '/.env', '/etc/passwd'])
def test_gateway_cannot_expose_model_files_or_unlisted_routes(path):
    app, calls = fixture_gateway()
    with TestClient(app) as client:
        assert client.get(path).status_code == 404
    assert not calls


def test_invalid_origin_oversized_body_and_full_model_queue_do_not_reach_backend():
    app, calls = fixture_gateway(available=False)
    with TestClient(app) as client:
        assert client.post('/api/manual/analyze', json={}).status_code == 403
        assert client.post('/api/manual/analyze', headers={'Origin': 'https://other.example'}, json={}).status_code == 403
        assert client.post('/api/manual/analyze', headers={'Origin': ORIGIN}, content=b'x'*(MAX_BODY+1)).status_code == 413
        assert client.post('/api/manual/analyze', headers={'Origin': ORIGIN}, json={}).status_code == 429
    assert not calls
