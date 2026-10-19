import test from 'node:test';
import assert from 'node:assert/strict';
import {candleSymbol, chartGeometry, quoteChange} from '../src/market.mjs';

test('monthly CFD chart matches the explicitly identified delivery month', () => {
  assert.equal(candleSymbol('Nov26'), 'CAPITALCOM:TRNLTTFMX2026');
  assert.equal(candleSymbol('Jan27'), 'CAPITALCOM:TRNLTTFMF2027');
  assert.equal(candleSymbol('ICE unknown'), null);
});
test('line chart uses real irregular timestamps and rejects invalid observations', () => {
  const chart = chartGeometry([
    {observed_at: '2026-10-19T08:00:00Z', value: 90},
    {observed_at: '2026-10-19T05:00:00Z', value: 88},
    {observed_at: '2026-10-19T06:00:00Z', value: 89},
    {observed_at: 'bad date', value: 92}, {observed_at: '2026-10-19T04:00:00Z', value: NaN},
  ]);
  assert.equal(chart.points.length, 3);
  assert.equal(chart.points[1].x, 304);
  assert.equal(chart.points[2].x, 888);
  assert.equal(chart.minimum, 88);
  assert.equal(chart.maximum, 90);
  assert.ok(!chart.line.includes('NaN'));
  assert.equal(chartGeometry([]), null);
  assert.ok(!chartGeometry([{observed_at: '2026-10-19T08:00:00Z', value: 90}]).line.includes('NaN'));
});
test('quote comparison cannot silently cross a contract roll', () => {
  const ref = {available: true, previous_price: 80, contract: 'Nov26'};
  assert.deepEqual(quoteChange({value: 84, contract: 'Nov26'}, ref), {delta: 4, percent: 5});
  assert.equal(quoteChange({value: 84, contract: 'Dec26'}, ref), null);
});

test('candles use observed OHLC instead of inventing ranges from closing prices', async () => {
  const {candleGeometry} = await import('../src/market.mjs');
  const item = {day: '2026-10-19', open: 80, high: 85, low: 79, close: 84};
  const chart = candleGeometry([item, {...item, high: 81}, {day: '2026-10-18', close: 83}]);
  assert.equal(chart.items.length, 1);
  assert.ok(chart.items[0].highY < chart.items[0].closeY);
  assert.ok(chart.items[0].lowY > chart.items[0].openY);
  assert.equal(chartGeometry([]), null);
  assert.equal(candleGeometry([{day: '2026-10-19', close: 83}]), null);
});
