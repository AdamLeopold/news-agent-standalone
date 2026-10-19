import {useEffect, useState, type ReactNode} from 'react';
import {request} from './api';
import {useLanguage} from './Localized';
import type {Result} from './types';
import {translateText} from './i18n.mjs';

type Translation = {id: string; status: string; result?: Result; error?: string};

export function AnalysisLanguage({id, result, part = 'brief', children}: {id: string; result: Result; part?: 'brief' | 'detail'; children: (result: Result) => ReactNode}) {
  const language = useLanguage();
  const [translated, setTranslated] = useState<{key: string; result: Result} | null>(null);
  const [error, setError] = useState('');
  const [stage, setStage] = useState('queued');
  const [retry, setRetry] = useState(0);
  const version = JSON.stringify(result);
  const key = `${id}/${part}/${version}`;
  useEffect(() => {
    // Keep a completed English view when toggling languages; avoid a loading flash.
    if (language !== 'en' || translated?.key === key) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    setTranslated(null); setError(''); setStage('queued');
    async function poll(value: Translation) {
      if (!active) return;
      setStage(value.status);
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
  if (translated?.key === key) {
    const english = {...translated.result, risk_flags: translated.result.risk_flags?.map((flag, index) => {
      const original = result.risk_flags?.[index];
      const policy = original ? translateText(original, 'en') : original;
      return policy && policy !== original ? policy : flag;
    })};
    return <>{children(english)}</>;
  }
  return <div className="translation-status" role="status">{error ? <><p>{error}</p><button className="button outline" onClick={() => setRetry(n => n + 1)}>Retry English analysis</button></> : <><span className="spinner"/><div><strong>{stage === 'queued' ? 'English analysis queued' : 'Preparing English analysis'}</strong><p>{stage === 'queued' ? 'Live briefs take priority in the local model queue. The English version is cached once ready.' : 'The original forecast, scores and evidence are preserved.'}</p></div></>}</div>;
}
