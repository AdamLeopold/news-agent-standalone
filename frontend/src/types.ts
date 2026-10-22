export type Direction = 'extreme_up' | 'up' | 'flat' | 'down' | 'extreme_down';
export type AnyRecord = Record<string, unknown>;
export interface Signal {
  direction?: Direction | null;
  evidence_status?: 'forecast' | 'insufficient_evidence';
  confidence?: number | string;
  summary?: string;
  score?: number;
  [key: string]: unknown;
}
export interface Result {
  news_impact?: Signal;
  close_forecast?: Signal;
  analysis?: {
    summary?: string;
    evidence?: unknown[];
    historical_comparisons?: unknown[];
    priced_in_comment?: string;
    [key: string]: unknown;
  };
  risk_flags?: string[];
  references?: {id: string; title?: string; url?: string}[];
  detail?: AnyRecord;
  _meta?: {as_of?: string; elapsed_seconds?: number; source_index?: {alias?: string; id: string; title?: string; url?: string}[]; [key: string]: unknown};
  [key: string]: unknown;
}
export interface Analysis {
  id: string;
  status: string;
  kind?: string;
  created_at?: string;
  completed_at?: string;
  result?: Result | null;
  error?: string | null;
  detail_status?: string;
  detail_result?: Result | null;
  detail_error?: string | null;
  review?: AnalysisReview | null;
  [key: string]: unknown;
}
export interface AnalysisReview {
  status: 'invalid' | 'cleared' | 'not_applicable';
  invalid_parts: ('brief' | 'detail')[];
  reason_code?: string;
  message_zh?: string;
  message_en?: string;
  parts?: Record<string, {status: string; reason_code: string; message_zh: string; message_en: string; reviewed_at: string}>;
}
export interface Article {
  id: string;
  title: string;
  summary?: string;
  source: string;
  url?: string;
  published_at?: string;
  published_date?: string;
  first_seen_at?: string;
  timestamp_precision?: string;
  trade_date?: string;
  analysis_id?: string | null;
  analysis_status?: string;
  analysis_created_at?: string;
  analysis_completed_at?: string;
  analysis?: Result | null;
  review?: AnalysisReview | null;
  ingestion_kind?: string;
  event_start?: string;
  event_status?: string;
}
export interface SourceState {
  source?: string;
  name?: string;
  id?: string;
  status?: string;
  last_success?: string;
  last_success_at?: string;
  last_error?: string;
  error?: string;
  article_count?: number;
  count?: number;
  [key: string]: unknown;
}
export interface Health {
  status: string;
  model?: {available: boolean; model: string; error?: string; [key: string]: unknown};
  counts?: {news: number; events: number; legacy_daily: number};
  sources?: SourceState[];
  collector?: {enabled: boolean; poll_seconds?: number};
  inference?: {enabled: boolean};
  [key: string]: unknown;
}
export interface HistoricalEvent {
  id?: string;
  title?: string;
  name?: string;
  summary?: string;
  category?: string;
  status?: string;
  state?: string;
  source_quality?: string;
  risk_flags?: string[];
  resolution_basis?: string;
  event_date?: string;
  start_date?: string;
  source?: string;
  url?: string;
  [key: string]: unknown;
}
export interface Dashboard {
  session: {trade_date: string; window_start: string; window_end: string; cutoff_utc: string; cutoff_local: string; display_timezone: string};
  daily: {article_count: number; source_count: number; summary?: string; news_ids?: string[]; [key: string]: unknown};
  latest_analysis?: Analysis | null;
  excluded_latest_analysis?: {id: string; completed_at?: string; review?: AnalysisReview | null} | null;
  important_events?: HistoricalEvent[];
  coverage?: AnyRecord;
  price_reference?: {available: boolean; verified?: boolean; previous_price?: number; contract?: string; contract_name?: string; price_date?: string; price_type?: string; source_url?: string; reference_source?: string; extreme_threshold?: number; sigma60?: number; risk_flags?: string[]; settlement_anchor?: {available: boolean; target_trade_date?: string; expected_previous_trade_date?: string; reference_date?: string; reason?: string}};
  [key: string]: unknown;
}
