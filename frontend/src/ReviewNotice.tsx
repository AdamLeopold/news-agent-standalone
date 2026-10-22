import {useLanguage} from './Localized';
import {analysisPartInvalid, analysisReviewTitle} from './display.mjs';
import type {AnalysisReview} from './types';

export function ReviewNotice({review, part = 'brief', onOpen}: {review?: AnalysisReview | null; part?: 'brief' | 'detail'; onOpen?: () => void}) {
  const en = useLanguage() === 'en';
  if (!analysisPartInvalid(review, part)) return null;
  const item = review?.parts?.[part];
  const priceBaseline = analysisReviewTitle(review, part) === '价格基准复核未通过';
  return <div className="error-box analysis-review" role="alert" data-review-part={part}>
    <strong>{en ? priceBaseline ? 'Price baseline review failed' : 'Source facts review failed' : analysisReviewTitle(review, part)}</strong>
    <p>{en ? item?.message_en || review?.message_en : item?.message_zh || review?.message_zh}</p>
    {onOpen && <button className="text-button" onClick={onOpen}>{en ? 'View original analysis' : '查看原始分析'}</button>}
  </div>;
}
