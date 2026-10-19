import {useEffect, useState, type ReactNode} from 'react';
import {request} from './api';
import {useLanguage} from './Localized';
import type {Result} from './types';

type Translation = {id: string; status: string; result?: Result; error?: string};

export function AnalysisLanguage({id, result, part = 'brief', children}: {id: string; result: Result; part?: 'brief' | 'detail'; children: (result: Result) => ReactNode}) {
  const language = useLanguage();
  const [translated, setTranslated] = useState<{key: string; result: Result} | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const version = JSON.stringify(result);
  const key = `${id}/${part}/${version}`;
  useEffect(() => {
    if (language !== 'en') return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    setTranslated(null); setError('');
    async function poll(value: Translation) {
      if (!active) return;
      if (value.status === 'completed' && value.result) {setTranslated({key, result: value.result}); return;}
      if (value.status === 'failed') {setError('English analysis could not be prepared. Please retry.'); return;}
      timer = setTimeout(() => {
        request<Translation>(`/api/translations/${encodeURIComponent(value.id)}`).then(poll).catch(() => {if (active) setError('English analysis is temporarily unavailable. Please retry.');});
      }, 2500);
    }
    const path = `/api/analyses/${encodeURIComponent(id)}/translation?part=${part}`;
    request<Translation>(path).then(value => {
      if (!active) return;
      if (value.status === 'not_requested' || (retry > 0 && value.status === 'failed')) {
        return request<Translation>(`${path}&retry=${retry > 0}`, {method: 'POST', body: '{}'}).then(poll);
      }
      return poll(value);
    })
      .catch(() => {if (active) setError('English analysis is temporarily unavailable. Please retry.');});
    return () => {active = false; clearTimeout(timer);};
  }, [id, part, language, version, key, retry]);
  if (language !== 'en') return <>{children(result)}</>;
  if (translated?.key === key) return <>{children(translated.result)}</>;
  return <div className="translation-status" role="status">{error ? <><p>{error}</p><button className="button outline" onClick={() => setRetry(n => n + 1)}>Retry English analysis</button></> : <><span className="spinner"/><div><strong>Preparing English analysis</strong><p>The original forecast, scores and evidence are preserved.</p></div></>}</div>;
}
