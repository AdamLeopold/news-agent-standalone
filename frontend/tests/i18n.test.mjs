import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {OriginalText, savedLanguage, translateNode, translateText} from '../src/i18n.mjs';

test('Chinese remains the default and saved language is validated', () => {
  assert.equal(savedLanguage(), 'zh');
  assert.equal(savedLanguage({getItem: () => 'en'}), 'en');
  assert.equal(savedLanguage({getItem: () => 'invalid'}), 'zh');
  assert.equal(savedLanguage({getItem: () => {throw new Error('unavailable');}}), 'zh');
});
test('the complete analysis workflow has translated UI labels', () => {
  for (const label of ['实时新闻','手动分析','详细分析','模型把握程度','数据覆盖报告','合约价格','采集失败','已检查 · 暂无新新闻','已公布/尚未开始','公告时段结束','实际流量恢复待核实']) {
    assert.notEqual(translateText(label, 'en'), label);
    assert.equal(translateText(label, 'zh'), label);
  }
  assert.equal(translateText('模型分析语言：中文', 'en'), 'AI analysis: English');
});
test('dynamic timestamps, measured delays and counts retain their values', () => {
  assert.equal(translateText('历史库 3,959 条', 'en'), 'Archive: 3,959 articles');
  assert.equal(translateText('归属交易日 2026-10-13', 'en'), 'Trade date 2026-10-13');
  assert.equal(translateText('发现延迟 1 分 30 秒', 'en'), 'Discovery latency 1 min 30 s');
  assert.equal(translateText('本结算窗口已收录 12 条新闻，来自 4 个来源。', 'en'), 'This settlement window includes 12 articles from 4 sources.');
});
test('brief and detail disagreement explains the review and frozen forecast in both languages', () => {
  const notice = '当日收盘方向预判 · 详析判断与简报不同：简报 基本维持 → 详析 涨；不改写已冻结预测。';
  assert.equal(translateText(notice, 'zh'), notice);
  assert.equal(translateText(notice, 'en'), 'Settlement direction outlook · Detail differs from the brief: brief Broadly flat → detail Up; frozen forecasts remain unchanged.');
  assert.equal(translateText('新闻价格压力 · 详析判断与简报不同：简报 异常暴涨 → 详析 异常暴跌；不改写已冻结预测。', 'en'), 'News price pressure · Detail differs from the brief: brief Extreme rally → detail Extreme sell-off; frozen forecasts remain unchanged.');
  assert.equal(translateText('详细分析是基于同一输入快照的按需复核，不代表新新闻。', 'en'), 'Detailed analysis is an on-demand review of the same input snapshot, not a new report.');
});
test('original news, model text, URLs and user input are excluded from UI translation', () => {
  const tree = React.createElement('div', {},
    React.createElement('h2', {}, '实时新闻'),
    React.createElement(OriginalText, {}, '实时新闻'),
    React.createElement(OriginalText, {language:'zh-CN'}, '开始分析'),
    React.createElement('input', {value:'开始分析', readOnly:true, placeholder:'原文标题或一句话概括','aria-label':'新闻正文'}),
    React.createElement('a', {href:'https://example.com/实时新闻'}, '查看证据来源'),
  );
  const html = renderToStaticMarkup(translateNode(tree, 'en'));
  assert.ok(html.includes('<h2>Live news</h2><span data-original-text="">实时新闻</span>'));
  assert.ok(html.includes('<span data-original-text="" lang="zh-CN">开始分析</span>'));
  assert.ok(html.includes('value="开始分析"'));
  assert.ok(html.includes('aria-label="Article text"'));
  assert.ok(html.includes('placeholder="Original title or a one-sentence description"'));
  assert.ok(html.includes('href="https://example.com/实时新闻"'));
});
test('translation is idempotent and preserves unrecognized source material', () => {
  const first = translateText('  采集失败  ', 'en');
  assert.equal(first, '  Collection failed  ');
  assert.equal(translateText(first, 'en'), first);
  assert.equal(translateText('原始新闻内容ABC123', 'en'), '原始新闻内容ABC123');
});
