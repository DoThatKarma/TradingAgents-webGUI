// Narrow runtime guards for untrusted backend payloads.
// LLM-derived content is data, never code — parsed defensively here.

import type {
  AgentStatusEvent,
  DecisionEvent,
  EventEnvelope,
  ReportEvent,
  ReportKey,
  RunEvent,
} from "./types";

const REPORT_KEYS: readonly ReportKey[] = [
  "market_report",
  "sentiment_report",
  "news_report",
  "fundamentals_report",
];

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function isReportKey(value: unknown): value is ReportKey {
  return typeof value === "string" && (REPORT_KEYS as readonly string[]).includes(value);
}

export function parseRunEvent(raw: unknown): RunEvent | null {
  if (!isRecord(raw)) return null;
  const type = raw["type"];

  if (type === "agent_status") {
    const agent = raw["agent"];
    const status = raw["status"];
    if (typeof agent === "string" && (status === "in_progress" || status === "completed")) {
      return { type: "agent_status", agent, status } satisfies AgentStatusEvent;
    }
    return null;
  }

  if (type === "report") {
    const key = raw["key"];
    const content = raw["content"];
    if (isReportKey(key) && typeof content === "string") {
      return { type: "report", key, content } satisfies ReportEvent;
    }
    return null;
  }

  if (type === "decision") {
    const signal = raw["signal"];
    const decision = raw["decision"];
    if (typeof signal === "string" && typeof decision === "string") {
      return { type: "decision", signal, decision } satisfies DecisionEvent;
    }
    return null;
  }

  return null;
}

/** Parse one SSE data frame (JSON envelope). Returns null for keep-alives/DONE/garbage. */
export function parseEnvelope(raw: string): EventEnvelope | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null; // includes the 'DONE' sentinel if a deployment sends it
  }
  if (!isRecord(parsed) || typeof parsed["seq"] !== "number") return null;
  const event = parseRunEvent(parsed["event"]);
  if (!event) return null;
  return { seq: parsed.seq, ts: typeof parsed["ts"] === "number" ? parsed.ts : 0, event };
}
