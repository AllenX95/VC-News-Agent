export type IntelligenceReviewStatus = "unread" | "reviewed" | "follow_up" | "ignored" | "archived";

export type IntelligenceItem = {
  content_id: number;
  title: string;
  summary: string | null;
  url: string;
  source_id: number;
  source_name: string;
  source_category: string;
  publish_time: string | null;
  crawl_time: string | null;
  review_status: IntelligenceReviewStatus;
  review_note: string | null;
  reviewed_at: string | null;
  relevance_score: number;
  relevance_confidence: number;
  relevance_reasons: string[];
  ai_related: boolean | null;
  llm_status: string;
  tags: Array<{ tag_key: string; tag_value: string }>;
  entities: Array<{ entity_id: number; entity_type: string; name: string }>;
};

export type IntelligencePage = {
  items: IntelligenceItem[];
  total: number;
  limit: number;
  offset: number;
  filters: {
    query: string;
    status: string;
    minimum_score: number | null;
    date: string | null;
  };
};

export type CrawlJob = {
  job_id: number;
  job_type: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  total_sources: number;
  succeeded_sources: number;
  failed_sources: number;
  message: string | null;
  created_at: string | null;
};

export type FinancingEventSource = {
  event_content_id: number;
  content_id: number;
  title: string;
  summary: string | null;
  url: string;
  source_name: string;
  publish_time: string | null;
  crawl_time: string | null;
  is_primary_source: boolean;
  match_score: number;
  match_reasons: string[];
  association_source: string;
};

export type FinancingEvent = {
  event_id: number;
  event_type: string;
  event_title: string;
  company_name: string;
  company_name_normalized: string;
  announced_date: string | null;
  financing_round: string | null;
  amount_original: string | null;
  amount_normalized: number | null;
  currency: string | null;
  investors: string[];
  lead_investors: string[];
  event_summary: string | null;
  confidence: number;
  review_status: "pending" | "confirmed" | "excluded";
  locked_by_user: boolean;
  reviewed_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  sources: FinancingEventSource[];
};

export type FinancingEventPage = {
  items: FinancingEvent[];
  total: number;
  limit: number;
  offset: number;
};

export type WatchItem = {
  watch_id: number;
  target_type: "financing_event" | "content";
  target_id: number;
  target_title_snapshot: string;
  target_summary_snapshot: string | null;
  target_available: boolean;
  priority: "high" | "medium" | "low";
  status: "watching" | "follow_up" | "paused" | "completed";
  reason: string | null;
  next_review_date: string | null;
  notes: string | null;
  created_at: string | null;
  updated_at: string | null;
  completed_at: string | null;
  is_due: boolean;
};

export type ReportVersion = {
  report_version_id: number;
  version_number: number;
  version_source: string;
  template_version: string;
  prompt_id: number | null;
  model_name: string | null;
  generation_status: string;
  generation_error: string | null;
  created_at: string | null;
  markdown_text?: string;
  input_snapshot?: unknown[];
};

export type Report = {
  report_id: number;
  report_type: string;
  title: string;
  period_start: string | null;
  period_end: string | null;
  status: "draft" | "reviewed" | "exported" | "archived";
  latest_version_number: number;
  last_generation_error: string | null;
  created_at: string | null;
  updated_at: string | null;
  inputs: Array<{
    report_input_id: number;
    target_type: string;
    target_id: number;
    display_order: number;
    included: boolean;
    snapshot: Record<string, unknown>;
  }>;
  versions: ReportVersion[];
  markdown_text?: string;
};

export type AutomationRunStatus = "running" | "success" | "partial" | "missing" | "corrupt" | "invalid" | string;

export type AutomationStatus = {
  status: AutomationRunStatus;
  target_date: string;
  running: boolean;
  lock: {
    present: boolean;
    status: string;
    run_id?: string | null;
    pid?: number | null;
    started_at?: string | null;
    error?: string;
  };
  latest: {
    status: AutomationRunStatus;
    target_date: string;
    run_id: string | null;
    started_at: string | null;
    finished_at: string | null;
    stages: Record<string, string>;
    counts: Record<string, number>;
    warnings: string[];
    error: string | null;
  } | null;
  latest_status: AutomationRunStatus | null;
  latest_run_id: string | null;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string | null;
  counts: Record<string, number>;
  warnings: string[];
  html_available: boolean;
  error: string | null;
};
