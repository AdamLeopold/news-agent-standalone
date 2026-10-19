export const directions = {
  extreme_up: {label: '异常暴涨', short: '暴涨', tone: 'up', symbol: '↗↗'},
  up: {label: '涨', short: '涨', tone: 'up', symbol: '↗'},
  flat: {label: '基本维持', short: '维持', tone: 'flat', symbol: '→'},
  down: {label: '跌', short: '跌', tone: 'down', symbol: '↘'},
  extreme_down: {label: '异常暴跌', short: '暴跌', tone: 'down', symbol: '↘↘'},
};

export function signalInfo(direction) {
  return directions[direction] || {label: '尚未分析', short: '待分析', tone: 'muted', symbol: '—'};
}

export function analysisDirectionChanges(brief, detail) {
  const fields = [['close_forecast', '当日收盘方向预判'], ['news_impact', '新闻价格压力']];
  return fields.flatMap(([key, label]) => {
    const before = brief?.[key]?.direction;
    const after = detail?.[key]?.direction;
    if (!Object.hasOwn(directions, before) || !Object.hasOwn(directions, after) || before === after) return [];
    return [{key, label, brief: directions[before].label, detail: directions[after].label}];
  });
}

export function displayConfidence(confidence) {
  if (confidence === undefined || confidence === null || confidence === '') return '未提供';
  if (typeof confidence === 'number' && Number.isFinite(confidence)) {
    const value = confidence <= 1 ? confidence * 100 : confidence;
    return `${Math.round(value)}%`;
  }
  const levels = {high: '高', medium: '中', low: '低'};
  return levels[confidence] || String(confidence);
}

export function statusInfo(status) {
  const table = {
    queued: ['排队分析', 'pending'], pending: ['等待分析', 'pending'],
    running: ['正在分析', 'pending'], processing: ['正在分析', 'pending'],
    completed: ['分析完成', 'good'], complete: ['分析完成', 'good'], done: ['分析完成', 'good'],
    failed: ['分析失败', 'bad'], error: ['分析失败', 'bad'],
    not_requested: ['未请求分析', 'muted'],
  };
  const [label, tone] = table[status] || ['等待分析', 'pending'];
  return {label, tone};
}

export function countdown(cutoff, now = Date.now()) {
  const remaining = new Date(cutoff).getTime() - now;
  if (!Number.isFinite(remaining)) return '—';
  if (remaining <= 0) return '已到结算时刻';
  const seconds = Math.floor(remaining / 1000);
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = seconds % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

export function sourceInfo(source) {
  const status = source.status || 'unknown';
  if (['error', 'failed', 'unavailable', 'blocked'].includes(status)) return {label: '采集失败', tone: 'bad'};
  if (['disabled', 'unsupported'].includes(status)) return {label: '未启用', tone: 'muted'};
  if (status === 'partial' || source.historical_status === 'partial' || source.historical_limited === true) return {label: '部分覆盖', tone: 'pending'};
  if (['running', 'fetching', 'pending'].includes(status)) return {label: '正在采集', tone: 'pending'};
  if (['ok', 'success', 'healthy', 'idle', 'no_news', 'empty'].includes(status)) {
    const noUpdates = source.last_created === 0 && source.last_revised === 0;
    return {label: status === 'no_news' || status === 'empty' || source.article_count === 0 || source.last_count === 0 || noUpdates ? '已检查 · 暂无新新闻' : '正常', tone: 'good'};
  }
  return {label: '尚未检查', tone: 'muted'};
}

export function safeUrl(value) {
  try {
    const url = new URL(value);
    return ['https:', 'http:'].includes(url.protocol) ? url.href : null;
  } catch { return null; }
}

export function dateDeltaSeconds(start, end) {
  if (!start || !end) return null;
  const seconds = (new Date(end).getTime() - new Date(start).getTime()) / 1000;
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : null;
}

export function formatDuration(seconds) {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) return '未提供';
  if (seconds < 60) return `${Math.round(seconds * 10) / 10} 秒`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分 ${Math.floor(seconds % 60)} 秒`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时 ${Math.floor(seconds % 3600 / 60)} 分`;
  return `${Math.floor(seconds / 86400)} 天 ${Math.floor(seconds % 86400 / 3600)} 小时`;
}

export function discoveryDelay(article) {
  if (article.ingestion_kind === 'backfill') return '历史回补，不计实时发现延迟';
  if (!['exact', 'second', 'minute', 'datetime'].includes(article.timestamp_precision)) return '发现延迟：原始时间精度不足';
  const seconds = dateDeltaSeconds(article.published_at, article.first_seen_at);
  return seconds === null ? '发现延迟：无法计算' : `发现延迟 ${formatDuration(seconds)}`;
}
