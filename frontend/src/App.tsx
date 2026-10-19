import {useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode} from 'react';
import {apiUrl, request} from './api';
import {analysisDirectionChanges, countdown, dateDeltaSeconds, discoveryDelay, displayConfidence, formatDuration, safeUrl, signalInfo, sourceInfo, statusInfo} from './display.mjs';
import type {Analysis, AnyRecord, Article, Dashboard, Health, HistoricalEvent, Result, Signal} from './types';
import {LanguageProvider, Localized, useLanguage} from './Localized';
import {AnalysisLanguage} from './AnalysisLanguage';
import {MarketPanel} from './MarketPanel';
import {OriginalText, savedLanguage} from './i18n.mjs';

function Icon({name, size = 18, className = ''}: {name: string; size?: number; className?: string}) {
  const paths: Record<string, ReactNode> = {
    live: <><path d="M3 12h4l3-8 4 16 3-8h4"/></>,
    edit: <><path d="m16 3 5 5-12 12H4v-5Z"/><path d="m13 6 5 5"/></>,
    arrow: <><path d="M5 12h14m-5-5 5 5-5 5"/></>,
    link: <><path d="M14 3h7v7m0-7L10 14"/><path d="M10 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-5"/></>,
    close: <path d="m6 6 12 12M6 18 18 6"/>,
    clock: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
    refresh: <><path d="M20 7v5h-5M4 17v-5h5"/><path d="M6.2 7a7 7 0 0 1 11-2L20 8M4 16l2.8 3a7 7 0 0 0 11-2"/></>,
    database: <><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/></>,
    sun: <><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.4 1.4m11.2 11.2L19 19M5 19l1.4-1.4M17.6 6.4 19 5"/></>,
    moon: <path d="M21 13a9 9 0 0 1-10-10 9 9 0 1 0 10 10Z"/>,
    layers: <><path d="m12 3 10 5-10 5L2 8Zm-9 10 9 5 9-5M3 18l9 5 9-5"/></>,
    shield: <><path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z"/><path d="m8 12 3 3 5-6"/></>,
    alert: <><path d="m12 3 10 18H2Z"/><path d="M12 9v4m0 4h.01"/></>,
    download: <><path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/></>,
    check: <path d="m5 12 4 4L19 6"/>,
    search: <><circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/></>,
  };
  return <Localized>{<svg width={size} height={size} className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] || paths.live}</svg>}</Localized>;
}

function dateText(value?: string, timeZone = 'Asia/Singapore', withDate = true) {
  if (!value) return '时间未提供';
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return value;
  const oldYear = date.getUTCFullYear() !== new Date().getUTCFullYear();
  return new Intl.DateTimeFormat('zh-CN', {timeZone, ...(withDate ? {...(oldYear ? {year: 'numeric'} as const : {}), month: '2-digit', day: '2-digit'} as const : {}), hour: '2-digit', minute: '2-digit', hour12: false}).format(date);
}

function contentText(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) return value.map(contentText).filter(Boolean).join('；');
  const item = value as AnyRecord;
  return ['summary', 'text', 'description', 'fact', 'reason', 'mechanism', 'comparison', 'analysis', 'title', 'name', 'assessment', 'scenario', 'signal'].map(key => item[key]).filter(Boolean).map(contentText).join(' · ') || Object.entries(item).filter(([key]) => !['id', 'url', 'source_url', 'news_id', 'event_id'].includes(key)).map(([key, val]) => `${key}: ${contentText(val)}`).join('；');
}

function ExternalLink({url, children, className = ''}: {url?: string; children: ReactNode; className?: string}) {
  const valid = safeUrl(url);
  return <Localized>{valid ? <a href={valid} target="_blank" rel="noopener noreferrer" className={className}>{children}<Icon name="link" size={13}/></a> : <span className={className}>{children}</span>}</Localized>;
}

function Badge({children, tone = 'muted', title}: {children: ReactNode; tone?: string; title?: string}) {
  return <Localized>{<span className={`badge ${tone}`} title={title}>{children}</span>}</Localized>;
}

function SignalDisplay({signal, label, compact = false}: {signal?: Signal; label: string; compact?: boolean}) {
  const language = useLanguage();
  const info = signalInfo(signal?.direction);
  const score = signal?.contextual_news_score;
  return <Localized>{<div className={`signal ${info.tone} ${compact ? 'compact' : ''}`}>
    <span className="eyebrow">{label}</span>
    <div className="signal-value"><span>{info.symbol}</span><strong>{info.label}</strong></div>
    {!compact && <><p>{signal?.summary ? <OriginalText language={language === 'en' ? 'en' : 'zh-CN'}>{signal.summary}</OriginalText> : '等待模型完成分析。这里仅展示实际返回的结论。'}</p>{typeof score === 'number' && <div className="score-row"><span>新闻分数 <b>{score > 0 ? '+' : ''}{score.toFixed(2)}</b></span><div className="score-track" aria-label={`新闻分数${score}，范围负3到3`}><i style={{left: `${Math.max(0, Math.min(100, (score + 3) / 6 * 100))}%`}}/></div><span>−3 / +3</span></div>}{signal && <div className="confidence">模型把握程度 <b>{displayConfidence(signal.confidence)}</b><span>未校准</span></div>}</>}
  </div>}</Localized>;
}

function EvidenceList({items, empty = '目前没有可用证据。', preserveOriginal = true}: {items?: unknown; empty?: string; preserveOriginal?: boolean}) {
  const values = Array.isArray(items) ? items : items ? [items] : [];
  return <Localized>{values.length ? <ul className="evidence-list">{values.map((value, index) => {
    const item = typeof value === 'object' && value !== null ? value as AnyRecord : {};
    const link = String(item.url || item.source_url || '');
    const tag = item.direction || item.type || item.category;
    return <Localized>{<li key={index}>{Boolean(tag) && <Badge tone={String(tag).includes('bull') ? 'up' : String(tag).includes('bear') ? 'down' : 'muted'}>{String(tag)}</Badge>}<div><p>{preserveOriginal ? <OriginalText>{contentText(value)}</OriginalText> : contentText(value)}</p>{link && <ExternalLink url={link} className="text-link">查看证据来源</ExternalLink>}</div></li>}</Localized>;
  })}</ul> : <p className="empty-copy">{empty}</p>}</Localized>;
}

function AnalysisSummary({result, openDetail, analysisId}: {result: Result; openDetail?: () => void; analysisId?: string}) {
  const language = useLanguage();
  if (analysisId) return <AnalysisLanguage id={analysisId} result={result}>{value => <AnalysisSummary result={value} openDetail={openDetail}/>}</AnalysisLanguage>;
  function resolveReferences(items: unknown) {
    return Array.isArray(items) ? items.map(item => {
      if (typeof item !== 'string') return item;
      const direct = result.references?.find(ref => ref.id === item.trim());
      if (direct) return {...direct, title: direct.title || `证据 ${direct.id}`};
      const alias = item.match(/\[([A-Z]\d+(?:\.\d+)?)\]/)?.[1];
      const reference = alias ? result._meta?.source_index?.find(ref => ref.alias === alias) : undefined;
      return reference ? {text: item, url: reference.url} : item;
    }) : items;
  }
  const directionalEvidence: [string, unknown, string][] = [['看涨证据', result.news_impact?.bullish_evidence, 'up'], ['看跌证据', result.news_impact?.bearish_evidence, 'down'], ['中和因素', result.news_impact?.neutralizing_evidence, 'flat']];
  return <Localized>{<div className="analysis-summary">
    <div className="analysis-language-note"><Badge>模型分析语言：中文</Badge></div>
    {result._meta?.as_of && <div className="analysis-asof"><Icon name="clock" size={13}/>依据截至 {dateText(result._meta.as_of)} SGT 的可用信息</div>}
    <div className="dual-signals"><SignalDisplay signal={result.news_impact} label="新闻价格压力"/><SignalDisplay signal={result.close_forecast} label="当日收盘方向预判"/></div>
    {result.analysis?.summary && <div className="analyst-note"><span className="eyebrow">分析简报</span><p><OriginalText language={language === 'en' ? 'en' : 'zh-CN'}>{result.analysis.summary}</OriginalText></p></div>}
    {result.analysis?.evidence && <EvidenceList items={resolveReferences(result.analysis.evidence)}/>}
    <div className="directional-evidence">{directionalEvidence.filter(([, items]) => Array.isArray(items) && items.length).map(([label, items, tone]) => <div className={tone} key={label}><span className="eyebrow">{label}</span><EvidenceList items={resolveReferences(items)}/></div>)}</div>
    {typeof result.close_forecast?.basis === 'string' && <div className="forecast-basis"><span className="eyebrow">收盘预判依据</span><p><OriginalText language={language === 'en' ? 'en' : 'zh-CN'}>{result.close_forecast.basis}</OriginalText></p></div>}
    {Boolean(result.close_forecast?.extreme_move_risk) && <div className="forecast-basis"><span className="eyebrow">异常波动条件</span><p><OriginalText language={language === 'en' ? 'en' : 'zh-CN'}>{contentText(result.close_forecast?.extreme_move_risk)}</OriginalText></p></div>}
    {result.risk_flags && result.risk_flags.length > 0 && <div className="risk-box"><Icon name="alert" size={16}/><div><strong>需要留意</strong><p><OriginalText language={language === 'en' ? 'en' : 'zh-CN'}>{result.risk_flags.map(contentText).join('；')}</OriginalText></p></div></div>}
    {openDetail && <button className="button outline full-detail" onClick={openDetail}>详细分析 <Icon name="arrow" size={16}/></button>}
  </div>}</Localized>;
}

function ArticleCard({article, onOpen, onRequested}: {article: Article; onOpen: (id: string) => void; onRequested: () => void}) {
  const [submitted, setSubmitted] = useState<{id: string; status: string} | null>(null);
  const [generating, setGenerating] = useState(false);
  const [generationError, setGenerationError] = useState('');
  const pendingOverride = submitted && submitted.id !== article.analysis_id;
  const analysisId = pendingOverride ? submitted.id : article.analysis_id;
  const analysisStatus = pendingOverride ? submitted.status : article.analysis_status;
  const state = statusInfo(analysisStatus);
  const backfill = article.ingestion_kind === 'backfill';
  const futureEvent = article.event_start && Date.parse(article.event_start) > Date.now() && !['Dismissed', 'Inactive'].includes(article.event_status || '');
  const briefSeconds = dateDeltaSeconds(article.analysis_created_at, article.analysis_completed_at);
  async function generate() {
    setGenerating(true); setGenerationError('');
    try {setSubmitted(await request<{id: string; status: string}>(`/api/news/${encodeURIComponent(article.id)}/analyze`, {method: 'POST', body: '{}'})); onRequested();}
    catch (e) {setGenerationError((e as Error).message);} finally {setGenerating(false);}
  }
  return <Localized>{<article className="news-card">
    <div className="news-meta"><span className="source-name">{article.source.replaceAll('_', ' ')}</span><time title="发布时间（新加坡时间）；仅日期信息保留原文日期">{article.published_at ? dateText(article.published_at) : article.published_date || '时间未提供'}</time></div>
    {(backfill || article.trade_date || futureEvent) && <div className="article-provenance">{backfill && <Badge>历史回补</Badge>}{futureEvent && <Badge tone="pending">已公布/尚未开始</Badge>}{article.trade_date && <span>归属交易日 {article.trade_date}</span>}</div>}
    <h3><ExternalLink url={article.url}><OriginalText>{article.title}</OriginalText></ExternalLink></h3>
    {article.summary && <p className="news-summary"><OriginalText>{article.summary}</OriginalText></p>}
    {article.timestamp_precision && !['second', 'minute', 'exact', 'datetime'].includes(article.timestamp_precision) && <span className="precision-note">{article.timestamp_precision === 'date' || article.timestamp_precision === 'day' ? '只有日期 · 不分配精确结算窗口' : '原始发布时间未能确认'}</span>}
    {article.analysis && !pendingOverride ? <div className="card-signals"><SignalDisplay label="新闻压力" signal={article.analysis.news_impact} compact/><SignalDisplay label="收盘预判" signal={article.analysis.close_forecast} compact/></div> : <div className="analysis-pending"><span className={`status-dot ${state.tone}`}/>{backfill && !analysisId ? '历史回补 · 未分析' : state.label}</div>}
    {generationError && <p role="alert" className="card-error">{generationError}</p>}
    <div className="news-card-footer"><span title={article.first_seen_at}>发现 {dateText(article.first_seen_at, 'Asia/Singapore', false)}</span>{!analysisId || ['failed', 'error'].includes(analysisStatus || '') ? <button className="text-button" disabled={generating} onClick={() => void generate()}>{generating ? '提交中…' : analysisId ? '重新分析' : '生成分析'}<Icon name="arrow" size={14}/></button> : <button className="text-button" onClick={() => onOpen(analysisId)}>{article.analysis && !pendingOverride ? '详细分析' : '查看进度'}<Icon name="arrow" size={14}/></button>}</div>
    <div className="news-latencies"><span>{discoveryDelay(article)}</span>{!pendingOverride && article.analysis && <span>模型耗时 {formatDuration(article.analysis._meta?.elapsed_seconds)}</span>}{!pendingOverride && briefSeconds !== null && <span>简报 {formatDuration(briefSeconds)}（含排队）</span>}{!pendingOverride && article.analysis_completed_at && <span>完成 {dateText(article.analysis_completed_at, 'Asia/Singapore', false)} SGT</span>}</div>
  </article>}</Localized>;
}

function EventCards({events}: {events: HistoricalEvent[]}) {
  return <Localized>{events.length ? <div className="event-cards">{events.map((event, index) => {
    const state = event.status || event.state;
    const unverifiedEnd = event.resolution_basis === 'announced_window_elapsed_unverified_flow';
    const stateName = unverifiedEnd && state === 'resolved' ? '公告时段结束' : state ? ({new: '新增', active: '持续', ongoing: '持续', revised: '修订', ended: '结束', resolved: '结束', historical: '历史归档', scheduled: '已公布/尚未开始'} as Record<string, string>)[state] || state : '';
    return <Localized>{<article key={event.id || index} className="event-card"><div className="event-heading"><span className="event-number">{String(index + 1).padStart(2, '0')}</span><div><h3>{event.title || event.name ? <OriginalText>{event.title || event.name}</OriginalText> : '历史事件'}</h3><span>{event.category || '天然气事件'} · {event.event_date || event.start_date || '日期待核对'}</span></div>{stateName && <Badge title={unverifiedEnd ? '实际流量恢复待核实' : undefined}>{stateName}</Badge>}</div><p><OriginalText>{event.summary || contentText(event)}</OriginalText></p>{event.source_quality === 'daily_aggregate_no_original_url' && <div className="event-quality">原每日聚合记录 · 无单条原始链接 · 旧事后标签仅供档案追溯</div>}{event.url && <ExternalLink url={event.url} className="text-link">事件来源</ExternalLink>}</article>}</Localized>;
  })}</div> : <Empty icon="layers" title="暂无相关事件" text="采集与分析后，相关历史事件会在这里显示。"/>}</Localized>;
}

function Empty({icon = 'live', title, text}: {icon?: string; title: string; text: string}) {
  return <Localized>{<div className="empty-state"><span className="empty-icon"><Icon name={icon} size={28}/></span><strong>{title}</strong><p>{text}</p></div>}</Localized>;
}

function PriceReference({price}: {price?: Dashboard['price_reference']}) {
  if (!price?.available) return <Localized>{<div className="price-reference unavailable"><Icon name="alert" size={15}/><span>行情参考尚未可用；缺少前一交易日合约价与波动率，异常幅度暂不评分。</span></div>}</Localized>;
  return <Localized>{<div className="price-reference-wrap"><div className="price-reference"><Icon name="live" size={16}/><div><span>行情参考</span><strong>{price.previous_price?.toFixed(2)}<small> €/MWh</small></strong><span>{price.contract_name || price.contract} · {price.price_date}</span></div><Badge tone={price.verified ? 'good' : 'pending'}>{price.verified ? '价格类型已核对' : '日价参考 · 未验证结算'}</Badge>{typeof price.extreme_threshold !== 'number' && <span className="price-gap">σ60 样本不足或有缺口</span>}</div><div className="price-reference-note"><ExternalLink url={price.source_url}>{price.reference_source || '原始价格来源'}</ExternalLink>{typeof price.extreme_threshold === 'number' && <span>参考异常门槛 ±{price.extreme_threshold.toFixed(2)} €/MWh</span>}<span>{price.verified ? '按已核对的价格类型评估' : '尚未验证为官方结算，不用于正式准确率评分'}</span>{price.risk_flags?.filter(flag => !flag.includes('尚未验证')).map(flag => <span key={flag}>{flag}</span>)}</div></div>}</Localized>;
}

function AnalysisTiming({analysis}: {analysis: Analysis}) {
  const total = dateDeltaSeconds(analysis.created_at, analysis.completed_at);
  return <Localized>{<div className="analysis-timing">{total !== null && <span>简报完成耗时 {formatDuration(total)}（含排队）</span>}{typeof analysis.result?._meta?.elapsed_seconds === 'number' && <span>简报模型耗时 {formatDuration(analysis.result._meta.elapsed_seconds)}</span>}{typeof analysis.detail_result?._meta?.elapsed_seconds === 'number' && <span>详析模型耗时 {formatDuration(analysis.detail_result._meta.elapsed_seconds)}</span>}</div>}</Localized>;
}

function DetailReviewNote({brief, detail}: {brief?: Result | null; detail: Result}) {
  const changes = analysisDirectionChanges(brief, detail);
  return <Localized>{<div className={`detail-review-note ${changes.length ? 'changed' : ''}`} role="note">
    <p>详细分析是基于同一输入快照的按需复核，不代表新新闻。</p>
    {changes.map(change => <p className="direction-change" key={change.key}>{`${change.label} · 详析判断与简报不同：简报 ${change.brief} → 详析 ${change.detail}；不改写已冻结预测。`}</p>)}
  </div>}</Localized>;
}

function DetailBody({result}: {result: Result}) {
  const language = useLanguage();
  const detail = result.detail || {};
  const scenarios = (detail.scenarios || {}) as AnyRecord;
  const sections: [string, string, unknown][] = [
    ['01', '本条新闻带来的新增事实', detail.new_facts || detail.facts],
    ['02', '如何传导至欧洲天然气与 TTF', detail.transmission || detail.ttf_transmission || detail.mechanism],
    ['03', '与本交易日新闻的合力与冲突', detail.daily_balance || detail.daily_conflicts],
    ['04', '历史大事件：相似处与差异', detail.historical_comparisons || result.analysis?.historical_comparisons],
    ['05', '市场是否已经计价', detail.priced_in_assessment || detail.priced_in || result.analysis?.priced_in_comment],
  ];
  return <Localized>{<><AnalysisSummary result={result}/><div className="detail-sections">{sections.map(([n, title, value]) => <section key={n}><div className="section-heading"><span>{n}</span><h3>{title}</h3></div><EvidenceList items={value} empty="模型未提供这一部分的证据。"/></section>)}
    <section><div className="section-heading"><span>06</span><h3>条件情景</h3></div><div className="scenario-grid">{[['上涨情景', scenarios.bullish || scenarios.bull, 'up'], ['基准情景', scenarios.base, 'flat'], ['下跌情景', scenarios.bearish || scenarios.bear, 'down']].map(([name, value, tone]) => <div className={`scenario ${tone}`} key={String(name)}><span className="eyebrow">{String(name)}</span><p>{value ? <OriginalText language={language === 'en' ? 'en' : 'zh-CN'}>{contentText(value)}</OriginalText> : '未提供条件情景。'}</p></div>)}</div></section>
    <section><div className="section-heading"><span>07</span><h3>结论的失效条件与信息缺口</h3></div><EvidenceList items={detail.invalidation_conditions}/>{Boolean(detail.limitations) && <div className="limitations"><strong>分析局限</strong><EvidenceList items={detail.limitations}/></div>}</section>
    <section><div className="section-heading"><span>08</span><h3>引用与证据来源</h3></div><EvidenceList items={detail.references || detail.sources} empty="这一分析没有返回来源引用。请结合原始新闻核对。"/></section>
  </div></>}</Localized>;
}

function Drawer({title, subtitle, close, children}: {title: string; subtitle?: string; close: () => void; children: ReactNode}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const old = document.activeElement as HTMLElement;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    ref.current?.focus();
    function keys(event: KeyboardEvent) {
      if (event.key === 'Escape') close();
      if (event.key === 'Tab') {
        const nodes = Array.from(ref.current?.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],input,textarea,select,[tabindex="0"]') || []);
        if (!nodes.length) {event.preventDefault(); return;}
        const first = nodes[0], last = nodes[nodes.length - 1];
        if (event.shiftKey && (document.activeElement === first || document.activeElement === ref.current)) {event.preventDefault(); last.focus();}
        else if (!event.shiftKey && document.activeElement === last) {event.preventDefault(); first.focus();}
      }
    }
    document.addEventListener('keydown', keys);
    return () => {document.body.style.overflow = previousOverflow; document.removeEventListener('keydown', keys); old?.focus();};
  }, [close]);
  return <Localized>{<div className="drawer-backdrop" onMouseDown={event => event.target === event.currentTarget && close()}><div className="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title" tabIndex={-1} ref={ref}><div className="drawer-header"><div><span className="eyebrow">TTF NEWS / RESEARCH</span><h2 id="drawer-title">{title}</h2>{subtitle && <p>{subtitle}</p>}</div><button className="icon-button" onClick={close} aria-label="关闭面板"><Icon name="close" size={22}/></button></div><div className="drawer-content">{children}</div></div></div>}</Localized>;
}

function AnalysisDrawer({id, close}: {id: string; close: () => void}) {
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const [retrying, setRetrying] = useState(false);
  const requested = useRef(false);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    requested.current = false;
    async function load() {
      try {
        const value = await request<Analysis>(`/api/analyses/${encodeURIComponent(id)}`);
        if (!active) return;
        setAnalysis(value); setError(''); setLoading(false);
        const briefReady = Boolean(value.result) && !['failed', 'error'].includes(value.status);
        if (briefReady && !requested.current && !value.detail_result && !['queued', 'running', 'failed', 'error'].includes(value.detail_status || '')) {
          requested.current = true;
          try {await request(`/api/analyses/${encodeURIComponent(id)}/detail`, {method: 'POST', body: '{}'});} catch (e) {if (active) setError((e as Error).message);}
          if (active) timer = setTimeout(load, 1500);
          return;
        }
        if (!value.detail_result && !['failed', 'error'].includes(value.status) && !['failed', 'error'].includes(value.detail_status || '')) timer = setTimeout(load, 2500);
      } catch (e) {if (active) {setError((e as Error).message); setLoading(false);}}
    }
    void load();
    return () => {active = false; clearTimeout(timer);};
  }, [id, retry]);
  async function retryDetail() {
    setRetrying(true);
    try {await request(`/api/analyses/${encodeURIComponent(id)}/detail`, {method: 'POST', body: '{}'}); setError(''); setRetry(value => value + 1);}
    catch (e) {setError((e as Error).message);} finally {setRetrying(false);}
  }
  const status = statusInfo(analysis?.status);
  const result = analysis?.detail_result || analysis?.result;
  return <Localized>{<Drawer title="详细分析" subtitle={`分析记录 ${id} · ${dateText(analysis?.created_at)}（新加坡）`} close={close}>
    {error && <div role="alert" className="error-box">{error}</div>}
    {loading ? <Loading text="正在读取分析记录…"/> : analysis ? <>
      <div className="analysis-record"><Badge tone={status.tone}>{status.label}</Badge><span>{analysis.kind === 'manual' ? '手动输入' : '新闻分析'} · {analysis.completed_at ? `完成于 ${dateText(analysis.completed_at)}` : '等待模型完成'}</span></div>
      <AnalysisTiming analysis={analysis}/>
      {analysis.error && <div className="error-box"><strong>简报分析失败</strong><p>{analysis.error}</p></div>}
      {result && (analysis.detail_result ? <AnalysisLanguage id={id} result={result} part="detail">{value => <><DetailReviewNote brief={analysis.result} detail={value}/><DetailBody result={value}/></>}</AnalysisLanguage> : <><AnalysisSummary result={result} analysisId={id}/>{['failed', 'error'].includes(analysis.detail_status || '') ? <div className="error-box"><strong>详细分析失败</strong><p>{analysis.detail_error || '详析任务未能完成。原有简报仍然可查。'}</p><button className="button outline retry-detail" disabled={retrying} onClick={() => void retryDetail()}>{retrying ? '重新提交中…' : '重新生成详细分析'}<Icon name="refresh" size={14}/></button></div> : <div className="detail-wait"><Loading text={analysis.detail_status === 'running' ? '模型正在整理完整证据分析…' : '详细分析已加入队列，完成后自动显示。'}/><p>实时简报优先处理。无需重复点击，结果将保存到本地。</p></div>}</>)}
      {!result && !analysis.error && <Empty icon="clock" title={status.label} text="模型完成简报后，将继续生成详细分析。页面会自动更新。"/>}
      <div className="method-note"><Icon name="shield" size={17}/><p>结论依据新闻证据与历史类比。模型把握程度尚未校准；单条新闻影响与实际当日涨跌分别记录。</p></div>
    </> : <Empty title="记录暂不可用" text="请关闭面板后重试，并检查后端是否正在运行。"/>}
  </Drawer>}</Localized>;
}

function Loading({text}: {text: string}) {return <Localized>{<div className="loading"><span className="spinner"/><span>{text}</span></div>}</Localized>;}

function ReportValue({value, depth = 0}: {value: unknown; depth?: number}) {
  if (value === null || value === undefined) return <Localized>{<span className="muted">未提供</span>}</Localized>;
  if (Array.isArray(value)) return <Localized>{value.length ? <div className="report-list">{value.map((item, index) => <div key={index}><ReportValue value={item} depth={depth + 1}/></div>)}</div> : <span className="muted">暂无记录</span>}</Localized>;
  if (typeof value === 'object') {
    const names: Record<string, string> = {by_month: '逐月覆盖', totals: '数据总量', generated_at: '生成时间', notes: '覆盖说明', date: '仅日期', unknown: '时间未知', source: '来源', source_name: '来源', status: '状态', month: '月份', count: '新闻数量', article_count: '新闻数量', total: '总量', missing_months: '未覆盖月份', failed: '失败', failures: '失败记录', timestamp_precision: '时间精度', date_only: '仅日期', exact: '精确时间', first_date: '起始日期', last_date: '最新日期', earliest: '起始日期', latest: '最新日期', last_success: '最后成功采集', last_error: '采集错误', sources: '来源覆盖', monthly: '逐月覆盖', summary: '摘要', legacy_daily: '原 NGW 每日数据', gaps: '覆盖缺口', updated_at: '更新时间', news: '逐条新闻', events: '事件库', target_start: '目标起点', target_end: '目标终点'};
    return <Localized>{<dl className={`report-fields depth-${Math.min(depth, 2)}`}>{Object.entries(value as AnyRecord).map(([key, val]) => <div key={key}><dt>{names[key] || key.replaceAll('_', ' ')}</dt><dd><ReportValue value={val} depth={depth + 1}/></dd></div>)}</dl>}</Localized>;
  }
  return <Localized>{<span>{typeof value === 'boolean' ? value ? '是' : '否' : String(value)}</span>}</Localized>;
}

function CoverageReport({data}: {data: unknown}) {
  const report = data as AnyRecord;
  const totals = (report.totals || report.counts || {}) as Record<string, number>;
  const sources = Array.isArray(report.sources) ? report.sources as AnyRecord[] : [];
  const months = Array.isArray(report.by_month) ? report.by_month as AnyRecord[] : Array.isArray(report.monthly) ? report.monthly as AnyRecord[] : [];
  return <Localized>{<div className="coverage-report"><div className="coverage-totals">{[['逐条新闻', totals.news], ['历史事件', totals.events], ['原每日数据', totals.legacy_daily], ['价格观测', totals.prices]].map(([title, value]) => <div key={title}><span>{title}</span><strong>{typeof value === 'number' ? value.toLocaleString() : '—'}</strong></div>)}</div>
    <div className="coverage-notes"><Icon name="alert" size={17}/><div><strong>覆盖与时间精度</strong><EvidenceList preserveOriginal={false} items={report.notes || ['未出现的月份不等于已确认没有新闻，请同时检查来源采集状态。']}/></div></div>
    <h3 className="report-section-title">各来源抓取进度</h3><div className="coverage-sources">{sources.map((source, index) => {const info = sourceInfo(source); return <Localized>{<div key={String(source.id || index)}><div className="coverage-source-title"><strong>{contentText(source.name || source.source)}</strong><Badge tone={info.tone}>{info.label}</Badge></div><p>目标区间 {contentText(source.historical_from || source.historical_requested_start) || '未提供'} — {contentText(source.historical_through) || '未提供'}</p>{sourceInfo(source).label === '部分覆盖' && <p className="source-coverage-note">历史档案未完全覆盖；未覆盖月份不能当作无新闻。</p>}{Boolean(source.last_error) && <p className="card-error">{sourceErrorText(String(source.last_error))}</p>}<div className="coverage-source-meta"><span>最近返回 {contentText(source.last_count) || '—'} 条</span><span>本次请求 {contentText(source.request_count) || '—'} 次</span><span>失败 {Array.isArray(source.failures) ? source.failures.length : 0} 次</span></div></div>}</Localized>;})}</div>
    <h3 className="report-section-title">逐月覆盖与时间精度</h3>{months.length ? <div className="coverage-table-scroll"><table className="coverage-table"><thead><tr><th>来源</th><th>月份</th><th>新闻</th><th>精确时间</th><th>仅日期</th><th>未知</th></tr></thead><tbody>{months.map((month, index) => <tr key={index}><td>{contentText(month.source).replaceAll('_', ' ')}</td><td>{contentText(month.month)}</td><td>{contentText(month.count)}</td><td>{contentText(month.exact)}</td><td>{contentText(month.date)}</td><td>{contentText(month.unknown)}</td></tr>)}</tbody></table></div> : <p className="empty-copy">尚无逐月记录。</p>}
    <details className="raw-report"><summary>查看完整采集记录与断点</summary><ReportValue value={data}/></details>
  </div>}</Localized>;
}

function ExportDownloads() {
  const groups = [
    {name: 'news', title: '逐条新闻', note: '原文链接、摘要、发布时间、发现时间与数据来源'},
    {name: 'daily', title: '每日新闻', note: '按 TTF 日结算窗口归集的全部历史新闻与汇总'},
    {name: 'events', title: '历史大事件', note: '事件状态、版本与来源质量'},
    {name: 'prices', title: '合约价格', note: '保留合约身份、价格类型及验证状态的参考行情'},
  ];
  return <Localized>{<div className="export-list">{groups.map(group => <div key={group.name} className="export-card"><span className="export-icon"><Icon name="database" size={24}/></span><div><strong>{group.title}</strong><p>{group.note}</p></div><div className="export-formats">{['csv', 'jsonl'].map(format => <a key={format} href={apiUrl(`/api/export/${group.name}.${format}`)} download aria-label={`导出${group.title} ${format.toUpperCase()}`}>{format.toUpperCase()}<Icon name="download" size={14}/></a>)}</div></div>)}</div>}</Localized>;
}

function sourceErrorText(error: string) {
  if (error.includes('429')) return '源站限制访问频率（429），当前抓取未完成。';
  if (/timeout|timed out/i.test(error)) return '源站连接超时，本次抓取未完成。';
  if (error.includes('403')) return '源站拒绝访问（403），当前抓取未完成。';
  if (error.includes('404')) return '原始页面未找到（404），当前抓取未完成。';
  if (error.length > 180) return `${error.slice(0, 177)}…`;
  return error;
}

function ResearchDrawer({kind, close}: {kind: 'events' | 'coverage' | 'export'; close: () => void}) {
  const [data, setData] = useState<unknown>();
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [eventLimit, setEventLimit] = useState(100);
  useEffect(() => {
    let active = true; setData(undefined); setError(''); setQuery(''); setEventLimit(100);
    if (kind !== 'export') request<unknown>(kind === 'events' ? '/api/events?limit=5000' : '/api/coverage').then(value => {if (active) setData(value);}).catch(e => {if (active) setError(e.message);});
    return () => {active = false;};
  }, [kind]);
  const titles = {events: '历史大事件库', coverage: '数据覆盖报告', export: '导出本地数据'};
  const events = ((data as {items?: HistoricalEvent[]})?.items || []);
  const eventTotal = (data as {total?: number})?.total ?? events.length;
  const filteredEvents = events.filter(event => `${event.title} ${event.summary} ${event.category} ${event.event_date}`.toLowerCase().includes(query.toLowerCase()));
  return <Localized>{<Drawer title={titles[kind]} subtitle={kind === 'events' ? '保留来源、事件状态和历史记录；未验证的标签会保持原有标记。' : kind === 'coverage' ? '逐月检查采集覆盖；采集失败与没有相关新闻分别记录。' : '从当前数据库生成 CSV / JSONL 文件。'} close={close}>
    {error ? <div className="error-box" role="alert">{error}</div> : kind === 'export' ? <ExportDownloads/> : !data ? <Loading text="正在读取本地记录…"/> : kind === 'events' ? <><div className="event-search-row"><label className="news-search"><Icon name="search" size={16}/><input aria-label="筛选历史事件" placeholder="搜索事件、类别或日期" value={query} onChange={event => {setQuery(event.target.value); setEventLimit(100);}}/></label><span>{filteredEvents.length} / {eventTotal} 条</span></div>{events.length < eventTotal && <p className="empty-copy">已加载 {events.length} 条；完整库共有 {eventTotal} 条，可导出全部记录。</p>}<EventCards events={filteredEvents.slice(0, eventLimit)}/>{filteredEvents.length > eventLimit && <button className="button outline load-more" onClick={() => setEventLimit(value => value + 100)}>再显示 100 条（当前 {Math.min(eventLimit, filteredEvents.length)} / {filteredEvents.length}）</button>}</> : <CoverageReport data={data}/>} 
  </Drawer>}</Localized>;
}

function ManualPage({onOpen, modelAvailable}: {onOpen: (id: string) => void; modelAvailable?: boolean}) {
  const [text, setText] = useState('');
  const [title, setTitle] = useState('');
  const [source, setSource] = useState('');
  const [publishedAt, setPublishedAt] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [polling, setPolling] = useState(false);
  useEffect(() => {
    if (!analysis?.id || !polling) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const value = await request<Analysis>(`/api/analyses/${encodeURIComponent(analysis!.id)}`);
        if (!active) return;
        setAnalysis(value);
        if (value.result || ['failed', 'error'].includes(value.status)) setPolling(false);
        else timer = setTimeout(load, 2000);
      } catch (e) {if (active) {setError((e as Error).message); setPolling(false);}}
    }
    void load();
    return () => {active = false; clearTimeout(timer);};
  }, [analysis?.id, polling]);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!text.trim()) {setError('请先输入新闻正文。'); return;}
    setBusy(true); setError(''); setAnalysis(null);
    try {
      const published_at = publishedAt ? new Date(`${publishedAt}:00+08:00`).toISOString() : undefined;
      const result = await request<Analysis>('/api/manual/analyze', {method: 'POST', body: JSON.stringify({text: text.trim(), title: title.trim() || undefined, source: source.trim() || undefined, published_at})});
      setAnalysis(result); setPolling(true);
    } catch (e) {setError((e as Error).message);} finally {setBusy(false);}
  }
  const state = statusInfo(analysis?.status);
  return <Localized>{<div className="manual-layout"><section className="panel manual-input"><div className="panel-heading"><div><span className="eyebrow">NEWS INPUT</span><h2>把一条新闻放进今天的市场</h2></div><Icon name="edit" size={22}/></div><p className="panel-description">结合当前结算窗口的累计新闻与历史大事件，判断新增信息如何影响 TTF。</p>
    <form onSubmit={submit}><label htmlFor="news-title">标题 <span>选填</span></label><input id="news-title" value={title} onChange={e => setTitle(e.target.value)} placeholder="原文标题或一句话概括" maxLength={500}/><label htmlFor="news-body">新闻正文 <span className="required">必填</span></label><textarea id="news-body" value={text} onChange={e => setText(e.target.value)} placeholder="粘贴新闻原文、公告或你希望分析的内容…" rows={12} required minLength={15} maxLength={30000}/><div className="form-row"><div><label htmlFor="news-source">来源 <span>选填</span></label><input id="news-source" value={source} onChange={e => setSource(e.target.value)} placeholder="媒体、机构或网址" maxLength={256}/></div><div><label htmlFor="news-time">发布时间 <span>新加坡 UTC+8 · 选填</span></label><input id="news-time" type="datetime-local" value={publishedAt} onChange={e => setPublishedAt(e.target.value)}/></div></div><p className="input-note">正文 15–30,000 字。未填写时间时使用提交时刻。手动内容保存在独立记录中。</p>{error && <div className="error-box" role="alert">{error}</div>}<button className="button primary submit" type="submit" disabled={busy || polling || text.trim().length < 15}>{busy ? '正在提交…' : polling ? '模型分析中…' : '开始分析'}{busy || polling ? <span className="spinner"/> : <Icon name="arrow" size={18}/>}</button>{modelAvailable === false && <p className="model-warning">模型当前不可用；任务状态会明确显示，完成后不会自动生成默认结论。</p>}</form>
  </section><section className="panel manual-output"><div className="panel-heading"><div><span className="eyebrow">ANALYST BRIEF</span><h2>分析结果</h2><p className="analysis-language-hint">模型分析语言：中文</p></div>{analysis && <Badge tone={state.tone}>{state.label}</Badge>}</div>{analysis?.result ? <><AnalysisTiming analysis={analysis}/><AnalysisSummary result={analysis.result} analysisId={analysis.id} openDetail={() => onOpen(analysis.id)}/></> : analysis?.error ? <div className="error-box"><strong>分析未完成</strong><p>{analysis.error}</p></div> : analysis ? <><Empty icon="clock" title={state.label} text="新闻已存入本地。正在检索当前交易日新闻与相关历史事件，等待模型返回简报。"/><div className="queued-record">记录编号 <code>{analysis.id}</code></div></> : <><Empty icon="search" title="从一条新闻开始" text="提交后，你会看到新闻价格压力、收盘方向预判以及对应证据。"/><div className="manual-process"><span><b>01</b> 当前交易日</span><i>→</i><span><b>02</b> 历史事件</span><i>→</i><span><b>03</b> 价格传导</span></div><div className="method-note"><Icon name="shield" size={18}/><p>这里展示模型的新闻判断。把握程度尚未校准，实际收盘结果单独评估。</p></div></>}</section></div>}</Localized>;
}

export default function App() {
  const [tab, setTab] = useState<'live' | 'manual'>('live');
  const [theme, setTheme] = useState(() => localStorage.getItem('ttf-theme') || 'dark');
  const [language, setLanguage] = useState(() => savedLanguage(localStorage));
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [articles, setArticles] = useState<Article[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [connected, setConnected] = useState(false);
  const [snapshotAt, setSnapshotAt] = useState('');
  const [freshAt, setFreshAt] = useState('');
  const [now, setNow] = useState(Date.now());
  const [analysisId, setAnalysisId] = useState<string | null>(null);
  const [research, setResearch] = useState<'events' | 'coverage' | 'export' | null>(null);
  const [newsFilter, setNewsFilter] = useState('');
  const refreshing = useRef(false);
  const mounted = useRef(true);
  const refresh = useCallback(async () => {
    if (refreshing.current) return;
    refreshing.current = true;
    const results = await Promise.allSettled([request<Dashboard>('/api/dashboard'), request<{items: Article[]; total: number}>('/api/news?scope=recent&limit=30'), request<Health>('/api/health')]);
    if (mounted.current) {
      const [dash, news, healthy] = results;
      if (dash.status === 'fulfilled') {setDashboard(dash.value); setError('');} else setError(dash.reason instanceof Error ? dash.reason.message : '暂时无法连接后端');
      if (news.status === 'fulfilled') {setArticles(news.value.items || []); setTotal(news.value.total || 0);}
      if (healthy.status === 'fulfilled') setHealth(healthy.value);
      if (results.some(result => result.status === 'fulfilled')) setFreshAt(new Date().toISOString());
      setLoading(false);
    }
    refreshing.current = false;
  }, []);
  useEffect(() => {document.documentElement.lang = language === 'en' ? 'en' : 'zh-CN'; localStorage.setItem('ttf-language', language); document.title = language === 'en' ? 'TTF News · Gas News Workbench' : 'TTF News · 天然气资讯工作台';}, [language]);
  useEffect(() => {document.documentElement.dataset.theme = theme; localStorage.setItem('ttf-theme', theme);}, [theme]);
  useEffect(() => {
    const changed = (event: Event) => setSnapshotAt((event as CustomEvent<string>).detail);
    window.addEventListener('ttf-snapshot', changed);
    return () => window.removeEventListener('ttf-snapshot', changed);
  }, []);
  useEffect(() => {
    mounted.current = true;
    void refresh();
    const poll = setInterval(() => void refresh(), 15000);
    const tick = setInterval(() => setNow(Date.now()), 1000);
    const stream = new EventSource(apiUrl('/api/stream'));
    stream.onopen = () => {setConnected(true); void refresh();};
    stream.onerror = () => setConnected(false);
    stream.addEventListener('update', () => void refresh());
    return () => {mounted.current = false; clearInterval(poll); clearInterval(tick); stream.close();};
  }, [refresh]);
  const closeAnalysis = useCallback(() => setAnalysisId(null), []);
  const closeResearch = useCallback(() => setResearch(null), []);
  const session = dashboard?.session;
  const latest = dashboard?.latest_analysis;
  const visibleNews = articles.filter(article => !newsFilter || `${article.title} ${article.summary} ${article.source}`.toLowerCase().includes(newsFilter.toLowerCase()));
  const sources = (health?.sources || []).filter(source => source.source !== 'eia_summary_repair');
  const collectorPaused = health?.collector?.enabled === false;
  const sourceErrors = sources.filter(source => sourceInfo(source).tone === 'bad').length;
  return <LanguageProvider language={language}>{<Localized>{<div className="app-shell"><header className="app-header"><a className="brand" href={import.meta.env.BASE_URL} aria-label="TTF News 首页"><span className="brand-mark"><Icon name="live" size={26}/></span><div><strong>TTF<span>NEWS</span></strong><small>天然气资讯工作台</small></div></a><div className="header-meta"><span className="local-chip">LOCAL WORKSPACE</span><button className="language-button" aria-label="切换界面语言" onClick={() => setLanguage(language === 'zh' ? 'en' : 'zh')}>{language === 'zh' ? 'EN' : '中文'}</button><span className="header-clock">{new Intl.DateTimeFormat('zh-CN', {timeZone: 'Asia/Singapore', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false}).format(now)} <small>SGT</small></span><button className="icon-button theme-toggle" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')} aria-label={theme === 'dark' ? '切换浅色主题' : '切换深色主题'}><Icon name={theme === 'dark' ? 'sun' : 'moon'} size={20}/></button></div></header>
    <div className="workspace-bar"><nav className="main-tabs" aria-label="主要工作区"><button className={tab === 'live' ? 'active' : ''} onClick={() => setTab('live')}><Icon name="live" size={17}/>实时新闻</button><button className={tab === 'manual' ? 'active' : ''} onClick={() => setTab('manual')}><Icon name="edit" size={17}/>手动分析</button></nav><div className="workspace-tools"><button onClick={() => setResearch('events')}><Icon name="layers" size={16}/><span>大事件库</span><b>{health?.counts?.events ?? '—'}</b></button><button onClick={() => setResearch('coverage')}><Icon name="database" size={16}/><span>数据覆盖</span></button><button onClick={() => setResearch('export')} aria-label="导出本地数据"><Icon name="download" size={16}/><span>导出</span></button></div></div>
    {snapshotAt && <div className="connection-banner" role="status"><Icon name="database" size={18}/><span><strong>当前展示已发布数据快照。</strong> <OriginalText>{dateText(snapshotAt)} SGT</OriginalText> · <span>实时后端未连接；新分析需要模型服务在线。</span></span><button onClick={() => void refresh()}>重试连接</button></div>}
    {error && <div className="connection-banner" role="alert"><Icon name="alert" size={18}/><span>后端连接异常：{error}。已显示的记录保留；正在自动重试。</span><button onClick={() => void refresh()}>重试连接</button></div>}
    <main><div hidden={tab !== 'manual'}><ManualPage onOpen={setAnalysisId} modelAvailable={health?.model?.available}/></div>{tab === 'live' && <div className="live-layout"><div className="live-main"><div className="page-title"><div><div className="eyebrow">EUROPEAN GAS / DUTCH TTF</div><h1>观察新闻，理解价格压力。</h1><p>按交易日结算归集 · 原始新闻、当日合力与历史类比</p></div><div className="market-label"><span className="status-dot good"/>新闻分析<span className="market-rule">€/MWh</span></div></div>
      <section className="session-panel" aria-label="当前交易窗口"><div className="session-main"><span className="eyebrow">当前结算交易日</span><strong>{session?.trade_date || '—'}</strong><div className="window-note"><Icon name="clock" size={14}/><span>{session ? `${dateText(session.window_start, 'Europe/Amsterdam')} → ${dateText(session.window_end, 'Europe/Amsterdam')}` : '正在读取交易窗口'}<b>阿姆斯特丹</b></span></div></div><div className="countdown"><span className="eyebrow">距离日结算</span><strong>{session ? countdown(session.cutoff_utc || session.window_end, now) : '—'}</strong><span>{session ? `${dateText(session.cutoff_utc || session.window_end, 'Asia/Singapore')} 新加坡` : '正常结算 17:15 · Europe/Amsterdam'}</span></div><div className="session-count" title="只统计可按精确发布时间归属本结算窗口的新闻；其他已发现新闻仍可作为分析背景。"><span className="eyebrow">本窗口新闻</span><strong>{dashboard?.daily?.article_count ?? '—'}<small>条</small></strong><span>{dashboard?.daily?.source_count ?? '—'} 个来源</span></div></section>
      <MarketPanel theme={theme}/><PriceReference price={dashboard?.price_reference}/><section className="panel market-panel"><div className="panel-heading"><div><span className="eyebrow">TODAY'S NEWS BALANCE</span><h2>当日新闻压力与收盘预判</h2><p className="analysis-language-hint">模型分析语言：中文</p></div>{latest && <Badge tone={statusInfo(latest.status).tone}>{statusInfo(latest.status).label}</Badge>}</div>{latest?.result ? <><AnalysisTiming analysis={latest}/><AnalysisSummary result={latest.result} analysisId={latest.id} openDetail={() => setAnalysisId(latest.id)}/></> : loading ? <Loading text="正在读取当前交易日…"/> : <Empty icon="live" title={latest ? statusInfo(latest.status).label : '等待当前窗口的第一份分析'} text={latest?.error || '新新闻会先出现在右侧信息栏，模型完成后，这里显示该窗口最新的分析结论。'}/>}
        {dashboard?.daily?.summary && <div className="daily-summary"><span className="eyebrow">本交易日新闻汇总</span><p>{dashboard.daily.summary}</p></div>}<div className="analysis-timestamp"><span>{latest?.completed_at ? `分析完成 ${dateText(latest.completed_at)} SGT` : latest?.created_at ? `任务创建 ${dateText(latest.created_at)} SGT` : '分析尚未完成'}</span><span>五档判断 · 新闻证据驱动</span></div>
      </section><div className="support-grid"><section className="panel events-panel"><div className="panel-heading"><div><span className="eyebrow">HISTORICAL CONTEXT</span><h2>相关大事件</h2></div><button className="text-button" onClick={() => setResearch('events')}>查看事件库<Icon name="arrow" size={14}/></button></div><EventCards events={(dashboard?.important_events || []).slice(0, 3)}/></section><section className="panel sources-panel"><div className="panel-heading"><div><span className="eyebrow">SOURCE HEALTH</span><h2>采集状态</h2></div>{sourceErrors > 0 && <Badge tone="bad">{`${sourceErrors} 个异常`}</Badge>}</div><div className="source-list">{sources.length ? sources.map((source, index) => {const info = sourceInfo(source); return <Localized>{<div className="source-row" key={source.source || source.name || index}><div><strong>{source.name || source.source || source.id || '新闻来源'}</strong><span title={source.last_success || source.last_success_at}>{source.last_success || source.last_success_at ? `上次成功 ${dateText(source.last_success || source.last_success_at)}` : '等待首次采集'}</span>{sourceInfo(source).label === '部分覆盖' && <p className="source-coverage-note">历史档案未完全覆盖，详情见数据覆盖报告。</p>}{(source.last_error || source.error) && <p className="source-error" title={source.last_error || source.error}>{sourceErrorText(source.last_error || source.error || '')}</p>}</div><span className={`source-state ${info.tone}`}><span className={`status-dot ${info.tone}`}/>{info.label}</span></div>}</Localized>;}) : <p className="empty-copy">尚未获得来源状态。</p>}</div><div className="model-health"><div><span className={`status-dot ${health?.model?.available ? 'good' : 'bad'}`}/><strong>{health?.model?.model || 'gpt-oss:20b'}</strong><Badge tone={health?.model?.available ? 'good' : 'muted'}>{health?.model?.available ? '本地可用' : '等待模型连接'}</Badge></div>{health?.model?.error && <p>{health.model.error}</p>}<span>单并发 · 实时简报优先</span></div></section></div>
      <div className="method-note workspace-note"><Icon name="shield" size={18}/><p>新闻价格压力与收盘方向分别判断。模型把握程度尚未校准；历史价格反应只在结果已知后用于复查。</p></div>
    </div><aside className="news-rail" aria-label="实时新闻信息栏"><div className="rail-header"><div><span className="eyebrow">LIVE NEWS FEED</span><h2>最新新闻 <span>{visibleNews.length}</span></h2><p className="feed-count">历史库 {total.toLocaleString()} 条</p></div><span className={`live-status ${connected && !collectorPaused ? 'good' : 'pending'}`}><span className={`status-dot ${connected && !collectorPaused ? 'good' : 'pending'}`}/>{collectorPaused ? '采集已暂停' : connected ? '消息推送正常' : '重连 / 定时同步'}</span></div><div className="rail-toolbar"><label className="news-search"><Icon name="search" size={16}/><input aria-label="筛选当前新闻" placeholder="搜索标题、摘要或来源" value={newsFilter} onChange={event => setNewsFilter(event.target.value)}/></label><button className="icon-button" onClick={() => void refresh()} aria-label="刷新新闻"><Icon name="refresh" size={17}/></button></div><div className="news-order"><span>按发现时间倒序</span><span>{collectorPaused ? '自动采集暂停' : `目标检查间隔 ${health?.collector?.poll_seconds || 60}s`} · SGT</span></div><div className="news-scroll">{loading ? <Loading text="正在读取最新新闻…"/> : visibleNews.length ? visibleNews.map(article => <ArticleCard key={article.id} article={article} onOpen={setAnalysisId} onRequested={() => void refresh()}/>) : <Empty icon="search" title={newsFilter ? '没有匹配的新闻' : '暂无已采集新闻'} text={newsFilter ? '尝试其他关键词，或清空筛选。' : '采集服务启动后，新文章会显示在这里。来源检查状态可在左侧查看。'}/>}</div><div className="rail-footer"><span className={`status-dot ${collectorPaused ? 'pending' : 'good'}`}/>{collectorPaused ? '自动采集暂停 · 仍可查看与手动分析' : '卡片先到达 · 分析随后补充'}<span>{freshAt ? `${dateText(freshAt, 'Asia/Singapore', false)} 同步` : '等待同步'}</span></div></aside></div>}</main>
    <footer className="app-footer"><span>TTF NEWS <b>·</b> 独立新闻研究工作台</span><span>本地运行 <b>·</b> gpt-oss:20b <b>·</b> 只展示实际分析</span></footer>{analysisId && <AnalysisDrawer id={analysisId} close={closeAnalysis}/>} {research && <ResearchDrawer kind={research} close={closeResearch}/>}</div>}</Localized>}</LanguageProvider>;
}
