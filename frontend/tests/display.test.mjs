import test from 'node:test';
import assert from 'node:assert/strict';
import {analysisDirectionChanges, analysisPartInvalid, analysisReviewTitle, countdown, dailyReferenceLabel, dateDeltaSeconds, discoveryDelay, displayConfidence, formatDuration, safeUrl, signalInfo, sourceInfo, statusInfo} from '../src/display.mjs';

test('invalid brief and independently reviewed detail stay separate without rewriting forecasts', () => {
  const review = Object.freeze({status:'invalid', invalid_parts:Object.freeze(['brief']), parts:{detail:{status:'cleared'}}});
  const result = Object.freeze({close_forecast:Object.freeze({direction:'down'})});
  assert.equal(analysisPartInvalid(review),true);
  assert.equal(analysisPartInvalid(review,'detail'),false);
  assert.equal(analysisPartInvalid({status:'cleared',invalid_parts:['brief']}),false);
  assert.equal(analysisPartInvalid(undefined),false);
  assert.equal(result.close_forecast.direction,'down');
  assert.equal(analysisReviewTitle({reason_code:'price_baseline_review_failed'}),'价格基准复核未通过');
  assert.equal(analysisReviewTitle({reason_code:'source_fact_review_failed'}),'来源事实复核未通过');
  assert.equal(analysisReviewTitle({reason_code:'price_baseline_review_failed',parts:{brief:{reason_code:'source_fact_review_failed'},detail:{reason_code:'price_baseline_review_failed'}}}),'来源事实复核未通过');
  assert.equal(analysisReviewTitle({reason_code:'source_fact_review_failed',parts:{detail:{reason_code:'price_baseline_review_failed'}}},'detail'),'价格基准复核未通过');
});

test('daily reference identity follows the verified target-session anchor without changing source facts', () => {
  const historical = Object.freeze({available:true, price_date:'2026-10-19', contract:'Nov26', settlement_anchor:Object.freeze({available:false})});
  assert.equal(dailyReferenceLabel(historical), '历史日价参考');
  assert.equal(dailyReferenceLabel({...historical, settlement_anchor:{available:true}}), '上一交易日日价');
  assert.equal(dailyReferenceLabel({available:true, price_date:'2026-10-19'}), '历史日价参考');
  assert.equal(historical.price_date, '2026-10-19');
  assert.equal(historical.contract, 'Nov26');
});
test('missing or malformed direction never becomes a prediction', () => {
  assert.equal(signalInfo(undefined).label, '尚未分析');
  assert.equal(signalInfo('invalid').tone, 'muted');
  assert.equal(signalInfo('extreme_up').label, '异常暴涨');
  assert.equal(signalInfo('extreme_down').label, '异常暴跌');
});
test('detail direction changes are explicit for both signals without modifying the brief', () => {
  const brief = Object.freeze({close_forecast: Object.freeze({direction: 'flat'}), news_impact: Object.freeze({direction: 'extreme_down'})});
  const detail = Object.freeze({close_forecast: Object.freeze({direction: 'up'}), news_impact: Object.freeze({direction: 'down'})});
  assert.deepEqual(analysisDirectionChanges(brief, detail), [
    {key: 'close_forecast', label: '当日收盘方向预判', brief: '基本维持', detail: '涨'},
    {key: 'news_impact', label: '新闻价格压力', brief: '异常暴跌', detail: '跌'},
  ]);
  assert.equal(brief.close_forecast.direction, 'flat');
  assert.deepEqual(analysisDirectionChanges(brief, brief), []);
  assert.deepEqual(analysisDirectionChanges(undefined, detail), []);
  assert.deepEqual(analysisDirectionChanges(brief, {close_forecast: {direction: 'invalid'}}), []);
  assert.deepEqual(analysisDirectionChanges({}, {}), []);
});
test('source failure is distinct from an empty successful poll', () => {
  assert.equal(sourceInfo({status: 'failed', article_count: 0}).label, '采集失败');
  assert.equal(sourceInfo({status: 'ok', article_count: 0}).label, '已检查 · 暂无新新闻');
  assert.equal(sourceInfo({status: 'unknown'}).label, '尚未检查');
  assert.equal(sourceInfo({status: 'partial'}).label, '部分覆盖');
  assert.equal(sourceInfo({status: 'ok', historical_limited: true}).label, '部分覆盖');
});
test('detail can acquire or lose a closing basis without rewriting an unavailable brief as flat', () => {
  const brief = Object.freeze({close_forecast: Object.freeze({direction: null, evidence_status: 'insufficient_evidence'})});
  const detail = {close_forecast: {direction: 'down', evidence_status: 'forecast'}};
  assert.deepEqual(analysisDirectionChanges(brief, detail), [
    {key: 'close_forecast', label: '当日收盘方向预判', brief: '暂缺收盘判断', detail: '跌'},
  ]);
  assert.deepEqual(analysisDirectionChanges(detail, brief), [
    {key: 'close_forecast', label: '当日收盘方向预判', brief: '跌', detail: '暂缺收盘判断'},
  ]);
  assert.equal(brief.close_forecast.direction, null);
});
test('confidence supports model fractions and qualitative levels without claiming calibration', () => {
  assert.equal(displayConfidence(0.72), '72%');
  assert.equal(displayConfidence('medium'), '中');
  assert.equal(displayConfidence(undefined), '未提供');
});
test('deadline handles missing and elapsed time without fabricating a timer', () => {
  assert.equal(countdown('2026-10-14T15:15:00Z', Date.parse('2026-10-14T15:14:00Z')), '00:01:00');
  assert.equal(countdown('2026-10-14T15:15:00Z', Date.parse('2026-10-14T15:15:00Z')), '已到结算时刻');
  assert.equal(countdown('invalid'), '—');
});
test('only source HTTP(S) links can be opened', () => {
  assert.equal(safeUrl('javascript:alert(1)'), null);
  assert.equal(safeUrl('file:///etc/passwd'), null);
  assert.equal(safeUrl('https://www.eia.gov/todayinenergy/'), 'https://www.eia.gov/todayinenergy/');
});
test('failed analysis preserves a visible failure state', () => {
  assert.equal(statusInfo('failed').label, '分析失败');
  assert.equal(statusInfo('running').label, '正在分析');
});
test('publication delay never treats backfill or date-only records as real-time latency', () => {
  const news = {published_at: '2026-10-14T01:00:00Z', first_seen_at: '2026-10-14T01:01:30Z', timestamp_precision: 'exact', ingestion_kind: 'live'};
  assert.equal(discoveryDelay(news), '发现延迟 1 分 30 秒');
  assert.equal(discoveryDelay({...news, ingestion_kind: 'backfill'}), '历史回补');
  assert.equal(discoveryDelay({...news, timestamp_precision: 'date'}), '发现延迟 —');
  assert.equal(discoveryDelay({...news, first_seen_at: '2026-10-13T23:00:00Z'}), '发现延迟 —');
});
test('model duration uses observed values and rejects absent or invalid times', () => {
  assert.equal(dateDeltaSeconds('2026-10-14T01:00:00Z', '2026-10-14T01:00:13Z'), 13);
  assert.equal(dateDeltaSeconds(undefined, '2026-10-14T01:00:13Z'), null);
  assert.equal(dateDeltaSeconds('invalid', '2026-10-14T01:00:13Z'), null);
  assert.equal(formatDuration(12.976), '13 秒');
  assert.equal(formatDuration(undefined), '未提供');
  assert.equal(formatDuration(-1), '未提供');
});
