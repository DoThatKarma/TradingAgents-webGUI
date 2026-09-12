// API contract types — mirror server/app/api/app.py models exactly.
// Field names are owned by the backend; do not rename without a backend change.

export const ASSET_TYPES = ["stock", "crypto", "polymarket"] as const;
export const PROVIDERS = ["direct", "ta_plugins"] as const;
export type AssetType = (typeof ASSET_TYPES)[number];
export type Provider = (typeof PROVIDERS)[number];

export interface RunConfig {
  ticker: string;
  date: string;
  asset_type: AssetType;
  provider: Provider;
  instructions?: string | null;
}

export interface RunCreated {
  job_id: string;
  status: string;
}

export type RunStatus = "queued" | "running" | "completed" | "cancelled" | "failed";

export const RUN_STATUSES: readonly RunStatus[] = [
  "queued",
  "running",
  "completed",
  "cancelled",
  "failed",
];

export interface JobStatus {
  job_id: string;
  ticker: string;
  date: string;
  asset_type: string;
  provider: string;
  effective_provider: string | null;
  has_instructions: boolean;
  status: RunStatus;
  created_at: number;
  error: string | null;
  event_count: number;
}

export interface DeleteRunResult {
  job_id: string;
  deleted: boolean;
  cancelled: boolean;
}

export interface HealthInfo {
  status: string;
}

// ---- SSE event payloads (server/app/engine/runner.py) ----

export type ReportKey =
  | "market_report"
  | "sentiment_report"
  | "news_report"
  | "fundamentals_report";

export interface AgentStatusEvent {
  type: "agent_status";
  agent: string;
  status: "in_progress" | "completed";
}

export interface ReportEvent {
  type: "report";
  key: ReportKey;
  content: string;
}

export interface DecisionEvent {
  type: "decision";
  signal: string;
  decision: string;
}

export type RunEvent = AgentStatusEvent | ReportEvent | DecisionEvent;

/** SSE data frame: JSON envelope {seq, ts, event} with SSE id = str(seq). */
export interface EventEnvelope {
  seq: number;
  ts: number;
  event: RunEvent;
}