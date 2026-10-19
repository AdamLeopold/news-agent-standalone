const apiBase = (import.meta.env.VITE_TTF_API_BASE || '').replace(/\/$/, '');
const snapshotBase = import.meta.env.VITE_TTF_SNAPSHOT_BASE || '';
let snapshotAt = '';
let manifest: Promise<{captured_at: string}> | null = null;
const snapshotCache = new Map<string, Promise<unknown>>();

function setSnapshot(asOf: string) {
  if (snapshotAt !== asOf) {
    snapshotAt = asOf;
    window.dispatchEvent(new CustomEvent('ttf-snapshot', {detail: asOf}));
  }
}

async function snapshot<T>(path: string): Promise<T> {
  const url = new URL(path, window.location.origin);
  const route = url.pathname.slice('/api/'.length);
  let file: string;
  if (route === 'news') file = `news/${url.searchParams.get('scope') || 'recent'}.json`;
  else if (/^analyses\/[A-Za-z0-9_.-]+$/.test(route)) file = `${route}.json`;
  else if (['health', 'dashboard', 'events', 'coverage', 'monitoring'].includes(route)) file = `${route}.json`;
  else throw new Error('当前快照没有这项记录；需要连接实时后端。');
  if (!snapshotCache.has(file)) snapshotCache.set(file, fetch(`${snapshotBase}${file}`).then(async r => {
    if (!r.ok) throw new Error('当前快照没有这项记录；需要连接实时后端。');
    return r.json();
  }).catch(error => {snapshotCache.delete(file); throw error;}));
  const data = await snapshotCache.get(file) as Record<string, unknown>;
  manifest ||= fetch(`${snapshotBase}manifest.json`).then(r => r.json());
  const meta = await manifest;
  setSnapshot(meta.captured_at);
  if (route === 'news' || route === 'events') {
    let items = data.items as Record<string, unknown>[];
    if (url.searchParams.has('trade_date')) items = items.filter(n => n.trade_date === url.searchParams.get('trade_date'));
    const offset = Math.max(0, Number(url.searchParams.get('offset')) || 0);
    const limit = Math.max(1, Math.min(route === 'news' ? 200 : 5000, Number(url.searchParams.get('limit')) || (route === 'news' ? 50 : 100)));
    return {...data, items: items.slice(offset, offset + limit)} as T;
  }
  if (route === 'health') return {...data, collector: {...data.collector as object, enabled: false}, model: {...data.model as object, available: false}} as T;
  return data as T;
}

export function apiUrl(path: string): string {
  if (!path.startsWith('/api/')) throw new Error('Invalid application API path');
  if (snapshotAt && path.startsWith('/api/export/')) return `${snapshotBase}exports/${path.slice('/api/export/'.length)}`;
  return `${apiBase}${path}`;
}

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const read = !options.method || ['GET', 'HEAD'].includes(options.method.toUpperCase());
  let response: Response;
  try {
    response = await fetch(`${apiBase}${path}`, {...options, ...(snapshotBase ? {signal: AbortSignal.timeout(5000)} : {}), headers: {'Content-Type': 'application/json', ...options.headers}});
    if (response.status >= 500 && read && snapshotBase) return snapshot<T>(path);
  } catch (error) {
    if (read && snapshotBase) return snapshot<T>(path);
    if (snapshotBase) throw new Error('实时分析尚未连接。已有分析与数据可查看，新分析需要连接模型后端。');
    throw error;
  }
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const body = await response.json();
      message = typeof body.detail === 'string' ? body.detail : body.message || message;
    } catch { /* The status remains useful for non-JSON failures. */ }
    throw new Error(message);
  }
  setSnapshot('');
  return response.json() as Promise<T>;
}
