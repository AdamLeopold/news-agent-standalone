import {Localized, useLanguage} from './Localized';
import {signalInfo} from './display.mjs';
import type {Analysis} from './types';

export function ForecastLane({analysis, onOpen}: {analysis?: Analysis | null; onOpen?: () => void}) {
  const en = useLanguage() === 'en';
  const direction = analysis?.result?.close_forecast?.direction;
  const ready = analysis?.status === 'completed' && ['extreme_up', 'up', 'flat', 'down', 'extreme_down'].includes(direction || '');
  const info = signalInfo(ready ? direction : undefined);
  const rising = direction === 'up' || direction === 'extreme_up';
  const falling = direction === 'down' || direction === 'extreme_down';
  const extreme = ready && direction?.startsWith('extreme_');
  const tilt = ready ? rising ? -28 : falling ? 28 : 0 : 0;
  const asOf = analysis?.result?._meta?.as_of || analysis?.completed_at;
  const time = asOf && Number.isFinite(Date.parse(asOf)) ? new Intl.DateTimeFormat(en ? 'en-GB' : 'zh-CN', {
    timeZone: 'Asia/Singapore', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(asOf)) : null;

  return <Localized>{<aside className={`forecast-lane ${info.tone} ${extreme ? 'extreme' : ''}`} data-direction={ready ? direction : 'pending'} aria-label="当前收盘预测">
    <div className="forecast-lane-heading"><span>AI OUTLOOK</span><i/></div>
    <span className="forecast-horizon">当日收盘</span>
    <div className="forecast-motion" aria-hidden="true">
      {ready ? <><div className="forecast-aura"/><div className="forecast-orbit"/>
        <svg viewBox="0 0 128 100" className="forecast-arrow-scene">
          <path d="M8 50H120" className="forecast-guide"/>
          <g className="forecast-arrow-drift"><g transform={`rotate(${tilt} 64 50)`}>
            <path className="forecast-arrow" d={extreme ? 'M24 50H84M71 37L84 50L71 63M86 37L99 50L86 63' : 'M24 50H99M83 34L99 50L83 66'}/>
            <circle cx="24" cy="50" r="2.5" className="forecast-origin"/>
          </g></g>
        </svg>
      </> : <div className="forecast-pending"><i/><i/><i/></div>}
    </div>
    <strong className="forecast-direction" key={direction || 'pending'}>{ready ? info.label : '分析中'}</strong>
    <span className="forecast-update">{time ? <>{time}<small>SGT</small></> : '等待更新'}</span>
    {ready && onOpen && <button className="forecast-detail" type="button" onClick={onOpen}>查看分析<svg viewBox="0 0 20 20" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="M4 10h12m-5-5 5 5-5 5"/></svg></button>}
  </aside>}</Localized>;
}
