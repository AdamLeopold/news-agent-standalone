import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';

async function loadApi(snapshot = true) {
  const env = {VITE_TTF_API_BASE: 'https://api.example.test',
    VITE_TTF_SNAPSHOT_BASE: snapshot ? '/news-agent-standalone/snapshot/' : ''};
  const source = (await readFile(new URL('../src/api.ts', import.meta.url), 'utf8'))
    .replaceAll('import.meta.env', `(${JSON.stringify(env)})`);
  const code = ts.transpileModule(source, {compilerOptions: {target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.ESNext}}).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(code + '\n//' + Math.random()).toString('base64')}`);
}

test('Pages keeps the real API result and does not substitute snapshot predictions', async () => {
  const saved = globalThis.fetch;
  globalThis.window = Object.assign(new EventTarget(), {location: {origin: 'https://adamleopold.github.io'}});
  const urls = [];
  globalThis.fetch = async url => {urls.push(url); return Response.json({status: 'completed', result: {close_forecast: {direction: 'down'}}});};
  try {
    const api = await loadApi();
    assert.equal((await api.request('/api/analyses/existing')).result.close_forecast.direction, 'down');
    assert.deepEqual(urls, ['https://api.example.test/api/analyses/existing']);
    assert.throws(() => api.apiUrl('https://unrelated.example/api'), /Invalid/);
  } finally {globalThis.fetch = saved;}
});

test('An offline API preserves published news, pagination and a dated snapshot notice', async () => {
  const saved = globalThis.fetch;
  globalThis.window = Object.assign(new EventTarget(), {location: {origin: 'https://adamleopold.github.io'}});
  let notice;
  window.addEventListener('ttf-snapshot', event => {notice = event.detail;});
  globalThis.fetch = async url => {
    if (url.startsWith('https://api.example.test')) throw new TypeError('offline');
    if (url.endsWith('manifest.json')) return Response.json({captured_at: '2026-10-19T07:39:14Z'});
    if (url.endsWith('health.json')) return Response.json({collector: {enabled: true}, model: {available: true}});
    return Response.json({items: [{id: 'n1'}, {id: 'n2'}, {id: 'n3'}], total: 3986});
  };
  try {
    const api = await loadApi();
    const data = await api.request('/api/news?scope=recent&offset=1&limit=1');
    assert.deepEqual(data.items, [{id: 'n2'}]);
    assert.equal(data.total, 3986);
    assert.equal(notice, '2026-10-19T07:39:14Z');
    assert.equal(api.apiUrl('/api/export/news.csv'), '/news-agent-standalone/snapshot/exports/news.csv');
    const health = await api.request('/api/health');
    assert.equal(health.collector.enabled, true, 'A website snapshot must not claim the local collector was paused');
    assert.equal(health.model.available, false, 'A snapshot must not claim new inference is available');
  } finally {globalThis.fetch = saved;}
});

test('Offline manual analysis reports unavailable instead of inventing an analysis', async () => {
  const saved = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async url => {calls.push(url); throw new TypeError('offline');};
  try {
    const api = await loadApi();
    await assert.rejects(api.request('/api/manual/analyze', {method: 'POST', body: '{}'}), /实时分析尚未连接/);
    assert.deepEqual(calls, ['https://api.example.test/api/manual/analyze']);
  } finally {globalThis.fetch = saved;}
});

test('offline English versions and market data use dated cached files', async () => {
  const saved = globalThis.fetch;
  globalThis.window = Object.assign(new EventTarget(), {location: {origin: 'https://adamleopold.github.io'}});
  const paths = [];
  globalThis.fetch = async url => {
    if (url.startsWith('https://api.example.test')) throw new Error('offline');
    paths.push(url);
    if (url.endsWith('manifest.json')) return Response.json({captured_at:'2026-10-19T09:00:00Z'});
    if (url.endsWith('market.json')) return Response.json({available:true, quote:{value:88.9}});
    return Response.json({status:'completed',result:{analysis:{summary:'Verified English version'}}});
  };
  try {
    const api = await loadApi();
    assert.equal((await api.request('/api/analyses/existing/translation?part=detail')).result.analysis.summary, 'Verified English version');
    assert.equal((await api.request('/api/market')).quote.value, 88.9);
    assert.ok(paths.includes('/news-agent-standalone/snapshot/analyses/existing.en.detail.json'));
  } finally {globalThis.fetch = saved;}
});
