import {useEffect, useId, useMemo, useRef, useState} from 'react';
import {request} from './api';
import {useLanguage} from './Localized';
import {candleGeometry, chartGeometry, quoteChange, type Point, type Candle} from './market.mjs';
import type {Dashboard} from './types';

type Quote = {contract: string; contract_name: string; value: number; observed_at: string; available_at: string; age_seconds: number; stale: boolean; source_url: string};
type Candles = {available: boolean; items: Candle[]; symbol?: string; contract_name?: string; source_url?: string; status?: string; last_success_at?: string};
type Market = {available: boolean; quote?: Quote; intraday: Point[]; candles?: Candles; reference: Dashboard['price_reference']; source_state?: {status?: string; last_error?: string}};

function DailyCandles({data, theme}: {data?: Candles; theme: string}) {
  const ref = useRef<SVGSVGElement>(null);
  const language = useLanguage();
  const en = language === 'en';
  const [range, setRange] = useState(60);
  const [hover, setHover] = useState<number | null>(null);
  const [exported, setExported] = useState<{url: string; name: string} | null>(null);
  const [exportError, setExportError] = useState(false);
  useEffect(() => () => {if (exported) URL.revokeObjectURL(exported.url);}, [exported]);
  const geometry = useMemo(() => candleGeometry((data?.items || []).slice(-range)), [data?.items, range]);
  const current = geometry?.items[hover ?? geometry.items.length - 1];
  async function saveImage() {
    if (!ref.current) return;
    setExportError(false);
    const background = theme === 'dark' ? '#0e1925' : '#ffffff';
    const foreground = theme === 'dark' ? '#d9e2ed' : '#24354a';
    const clone = ref.current.cloneNode(true) as SVGSVGElement;
    clone.querySelectorAll('.chart-gridline').forEach(node => node.setAttribute('stroke', theme === 'dark' ? '#22303f' : '#e2e8ef'));
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="440" viewBox="0 0 900 330"><rect width="900" height="330" fill="${background}"/><text x="24" y="27" fill="${foreground}" font-family="Arial" font-size="13">TTF CFD ${data?.contract_name || ''} · Capital.com · ASK · Daily UTC</text><g transform="translate(0,45)">${clone.innerHTML}</g><text x="24" y="320" fill="${foreground}" font-family="Arial" font-size="10">Reference OHLC · not ICE settlement · ${geometry?.items[0]?.day || ''} — ${geometry?.items.at(-1)?.day || ''}</text></svg>`;
    const url = URL.createObjectURL(new Blob([svg], {type: 'image/svg+xml'}));
    const image = new Image();
    image.onload = () => {
      const canvas = document.createElement('canvas'); canvas.width = 1800; canvas.height = 660;
      const context = canvas.getContext('2d');
      if (context) {
        context.drawImage(image, 0, 0, 1800, 660);
        canvas.toBlob(blob => {
          if (blob) setExported({url: URL.createObjectURL(blob), name: `TTF-CFD-${data?.contract_name || 'daily'}-${new Date().toISOString().slice(0, 10)}.png`});
          else setExportError(true);
        }, 'image/png');
      }
      URL.revokeObjectURL(url);
    };
    image.onerror = () => {URL.revokeObjectURL(url); setExportError(true);};
    image.src = url;
  }
  if (!geometry || !current) return <div className="chart-empty">{en ? 'Waiting for genuine daily OHLC. No candles are inferred from daily closes.' : '等待真实日线 OHLC，不用每日单一收盘价拼造 K 线。'}</div>;
  return <div className="native-candles" onPointerLeave={() => setHover(null)}><div className="candle-toolbar"><span><strong>{data?.contract_name}</strong> · CFD ASK · UTC D1</span><div>{[20, 60, 90].map(n => <button type="button" key={n} className={range === n ? 'active' : ''} onClick={() => {setRange(n); setHover(null);}}>{n}D</button>)}<button type="button" className="chart-save" onClick={() => void saveImage()}>{en ? 'Save PNG ↓' : '保存图片 ↓'}</button></div></div>
    <div className="candle-readout"><time>{current.day}</time>{(['open', 'high', 'low', 'close'] as const).map((key, i) => <span key={key}>{['O', 'H', 'L', 'C'][i]} <b>{current[key].toFixed(3)}</b></span>)}{current.forming && <small>{en ? 'In progress' : '尚未收盘'}</small>}</div>
    <svg ref={ref} viewBox="0 0 900 250" role="img" aria-label={en ? 'Daily TTF CFD candlestick chart with actual OHLC' : '基于真实 OHLC 的 TTF CFD 日 K 线图'} onPointerMove={event => {
      const rect = event.currentTarget.getBoundingClientRect(); const x = (event.clientX - rect.left) / rect.width * 900;
      let nearest = 0; geometry.items.forEach((p, i) => {if (Math.abs(p.x - x) < Math.abs(geometry.items[nearest].x - x)) nearest = i;}); setHover(nearest);
    }}>
      {geometry.ticks.map(t => <g key={t.y}><line x1="0" x2="840" y1={t.y} y2={t.y} className="chart-gridline"/><text x="850" y={t.y + 3} fill="#8292a5" fontSize="9" fontFamily="Arial">{t.value.toFixed(2)}</text></g>)}
      {geometry.items.map(bar => {
        const color = bar.close >= bar.open ? (theme === 'dark' ? '#71ddba' : '#279f83') : (theme === 'dark' ? '#e6a178' : '#c98157');
        return <g key={bar.day} opacity={hover !== null && geometry.items[hover]?.day !== bar.day ? .6 : 1}><line x1={bar.x} x2={bar.x} y1={bar.highY} y2={bar.lowY} stroke={color} strokeWidth="1.2"/><rect x={bar.x - geometry.bodyWidth / 2} y={Math.min(bar.openY, bar.closeY)} width={geometry.bodyWidth} height={Math.max(1.3, Math.abs(bar.openY - bar.closeY))} rx="1" fill={color}/></g>;
      })}
      {hover !== null && <line x1={current.x} x2={current.x} y1="0" y2="250" className="chart-crosshair"/>}
    </svg><div className="chart-time-axis"><span>{geometry.items[0].day}</span><span>{en ? `${geometry.items.length} actual daily bars · EUR/MWh` : `${geometry.items.length} 根真实日 K · EUR/MWh`}</span><span>{geometry.items.at(-1)!.day}</span></div>
    {exportError && <p role="alert">{en ? 'The chart image could not be generated. Please retry.' : '图表图片生成失败，请重试。'}</p>}
    {exported && <dialog className="chart-export" aria-label={en ? 'Chart image preview' : '图表图片预览'} ref={node => {if (node && !node.open) node.showModal();}} onClose={() => setExported(null)}><div><strong>{en ? 'TTF daily chart · PNG' : 'TTF 日 K 图 · PNG'}</strong><button type="button" onClick={() => setExported(null)} aria-label={en ? 'Close image preview' : '关闭图片预览'}>×</button></div><img src={exported.url} alt={en ? 'Exported TTF CFD daily candlestick image' : '已生成的 TTF CFD 日 K 线图片'}/><a className="button primary" href={exported.url} download={exported.name}>{en ? 'Download PNG ↓' : '下载 PNG ↓'}</a></dialog>}
  </div>;
}

export function MarketPanel({theme}: {theme: string}) {
  const language = useLanguage();
  const en = language === 'en';
  const [market, setMarket] = useState<Market | null>(null);
  const [error, setError] = useState(false);
  const [mode, setMode] = useState<'intraday' | 'daily'>('intraday');
  const [hover, setHover] = useState<number | null>(null);
  const gradient = useId().replaceAll(':', '');
  useEffect(() => {
    let active = true;
    const refresh = () => request<Market>('/api/market').then(data => {if (active) {setMarket(data); setError(false);}}).catch(() => {if (active) setError(true);});
    void refresh();
    const timer = setInterval(() => void refresh(), 30000);
    return () => {active = false; clearInterval(timer);};
  }, []);
  const geometry = useMemo(() => chartGeometry(market?.intraday || []), [market?.intraday]);
  const quote = market?.quote;
  const reference = market?.reference;
  const change = quoteChange(quote, reference);
  const up = !change || change.delta >= 0;
  const current = hover !== null ? geometry?.points[hover] : undefined;
  const time = (value: string) => new Intl.DateTimeFormat(en ? 'en-GB' : 'zh-CN', {timeZone: 'Europe/Amsterdam', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false}).format(new Date(value));
  const hour = (value: string) => new Intl.DateTimeFormat('en-GB', {timeZone: 'Europe/Amsterdam', hour: '2-digit', minute: '2-digit', hour12: false}).format(new Date(value));
  return <section className="panel price-market-panel" aria-label={en ? 'TTF market prices and charts' : 'TTF 行情与图表'}>
    <div className="price-market-heading"><div><span className="eyebrow">DUTCH TTF / MARKET MONITOR</span><h2>{en ? 'Price in focus' : '价格观察'}<span>{quote?.contract_name || reference?.contract_name || 'TTF'} <small>· EUR/MWh</small></span></h2></div><span className="market-delay"><i/>{en ? 'DELAYED ≥15 MIN' : '延迟 ≥15 分钟'}</span></div>
    <div className="quote-row"><div className={`quote-value ${up ? 'positive' : 'negative'}`}><strong key={quote?.value}>{quote?.value.toFixed(3) || '—'}</strong><div>{change ? <><b>{change.delta >= 0 ? '+' : ''}{change.delta.toFixed(3)} <span>({change.percent >= 0 ? '+' : ''}{change.percent.toFixed(2)}%)</span></b><small>{en ? 'vs. same-contract prior daily reference' : '对比同合约前一日参考价'}</small></> : <small>{en ? 'Waiting for an identified ICE quote' : '等待已确认合约的 ICE 报价'}</small>}</div></div><div className="quote-clock"><span>ICE · {en ? 'Last trade' : '最新成交参考'}</span><strong>{quote ? time(quote.observed_at) : '—'}</strong><small>{en ? 'Amsterdam time' : '阿姆斯特丹时间'}</small></div></div>
    <div className="chart-controls" role="tablist" aria-label={en ? 'Chart view' : '图表视图'}><button id="intraday-tab" type="button" role="tab" aria-selected={mode === 'intraday'} aria-controls="market-chart" className={mode === 'intraday' ? 'active' : ''} onClick={() => setMode('intraday')}>{en ? 'Intraday' : '盘中分时'}<small>ICE</small></button><button id="daily-tab" type="button" role="tab" aria-selected={mode === 'daily'} aria-controls="market-chart" className={mode === 'daily' ? 'active' : ''} onClick={() => setMode('daily')}>{en ? 'Daily candles' : '日 K 线'}<small>CFD</small></button><span>{mode === 'intraday' ? (en ? 'Sampled prices · actual timestamps' : '采样报价 · 保留真实时间') : (en ? 'Capital.com · same delivery month' : 'Capital.com · 同交割月')}</span></div>
    {(error || market?.source_state?.status === 'failed' || quote?.stale || Boolean(quote && Date.now() - Date.parse(quote.observed_at) > 3600000)) && <div className="market-stale" role="status">{en ? 'Latest cached quote shown. The feed may be delayed, unavailable or the market closed; check its timestamp.' : '当前显示最近缓存报价。来源可能延迟、暂不可用或已休市，请核对报价时间。'}</div>}
    {mode === 'daily' && (market?.candles?.status === 'failed' || Boolean(market?.candles?.last_success_at && Date.now() - Date.parse(market.candles.last_success_at) > 1800000)) && <div className="market-stale" role="status">{en ? 'Daily OHLC could not be refreshed. Cached candles retain their original update time.' : '日 K 行情暂未刷新，缓存 K 线保留原始更新时间。'}</div>}
    <div id="market-chart" role="tabpanel" aria-labelledby={mode === 'intraday' ? 'intraday-tab' : 'daily-tab'} className={`chart-body ${mode}`}>
      {mode === 'intraday' ? geometry ? <div className={`intraday-chart ${up ? 'positive' : 'negative'}`} onPointerLeave={() => setHover(null)}>
        <div className="chart-topline"><span>{current ? `${time(current.observed_at)} · ${current.value.toFixed(3)}` : `${en ? 'Range' : '区间'} ${geometry.minimum.toFixed(3)} — ${geometry.maximum.toFixed(3)}`}</span><span>EUR/MWh</span></div>
        <svg viewBox="0 0 900 240" preserveAspectRatio="none" role="img" aria-label={en ? 'ICE delayed TTF intraday price line' : 'ICE 延迟 TTF 盘中价格曲线'} onPointerMove={event => {
          const rect = event.currentTarget.getBoundingClientRect();
          const x = (event.clientX - rect.left) / rect.width * 900;
          let nearest = 0;
          geometry.points.forEach((p, i) => {if (Math.abs(p.x - x) < Math.abs(geometry.points[nearest].x - x)) nearest = i;});
          setHover(nearest);
        }}>
          <defs><linearGradient id={gradient} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="currentColor" stopOpacity=".22"/><stop offset="100%" stopColor="currentColor" stopOpacity="0"/></linearGradient></defs>
          {[36, 108, 180, 239].map(y => <line key={y} x1="0" x2="900" y1={y} y2={y} className="chart-gridline"/>)}
          <path d={geometry.area} fill={`url(#${gradient})`}/><path className="chart-price-line" d={geometry.line} fill="none" stroke="currentColor" strokeWidth="2.3" vectorEffect="non-scaling-stroke"/>
          {current && <><line x1={current.x} x2={current.x} y1="0" y2="240" className="chart-crosshair"/><circle cx={current.x} cy={current.y} r="4" fill="currentColor"/></>}
          {!current && <circle className="last-price-dot" cx={geometry.points.at(-1)!.x} cy={geometry.points.at(-1)!.y} r="3.5" fill="currentColor"/>}
        </svg><div className="chart-time-axis"><span>{hour(geometry.points[0].observed_at)}</span><span>{en ? 'Amsterdam · overnight session included' : '阿姆斯特丹 · 含隔夜交易时段'}</span><span>{hour(geometry.points.at(-1)!.observed_at)}</span></div>
      </div> : <div className="chart-empty">{en ? 'Waiting for genuine intraday observations. No simulated prices are displayed.' : '等待真实盘中报价，不使用模拟价格填充。'}</div> : <DailyCandles data={market?.candles} theme={theme}/>}
    </div>
    <div className="chart-source-note">{mode === 'daily' ? <><a href={market?.candles?.source_url} target="_blank" rel="noopener noreferrer">{en ? 'Capital.com · TTF CFD ASK OHLC ↗' : 'Capital.com · TTF CFD 卖方 OHLC ↗'}</a><span>{en ? 'Broker reference, not ICE futures · refreshes every 10 min · current bar is incomplete · excluded from settlement scoring.' : '经纪商参考行情，与 ICE 期货不同 · 每 10 分钟更新 · 当日日 K 未完成 · 不用于结算评分。'}</span>{market?.candles?.last_success_at && <small>{en ? 'Updated' : '更新'} {time(market.candles.last_success_at)}</small>}</> : <><a href={quote?.source_url || 'https://www.ice.com/products/27996665/Dutch-TTF-Gas-Futures/data'} target="_blank" rel="noopener noreferrer">ICE {en ? 'public delayed quotes ↗' : '公开延迟报价 ↗'}</a><span>{en ? 'At least 15 minutes delayed · checked every 60s · not an official settlement feed.' : '至少延迟 15 分钟 · 每 60 秒检查 · 不作为官方结算行情。'}</span>{quote && <small>{en ? 'Received' : '本机获取'} {time(quote.available_at)}</small>}</>}</div>
  </section>;
}
