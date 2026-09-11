// Pure SSE-event → UI-state reducer for the analysis pipeline.
// Stage order mirrors server/app/engine/runner.py _STAGE_EVIDENCE exactly.

import type { EventEnvelope, ReportKey, RunEvent } from "../api/types";

export const PIPELINE_STAGES: readonly string[] = [
  "Market Analyst",
  "Sentiment Analyst",
  "News Analyst",
  "Fundamentals Analyst",
  "Bull Researcher",
  "Bear Researcher",
  "Research Manager",
  "Trader",
  "Aggressive Analyst",
  "Conservative Analyst",
  "Neutral Analyst",
  "Portfolio Manager",
];

export type StageState = "pending" | "in_progress" | "completed";

export type ReportMap = Partial<Record<ReportKey, string>>;

export interface PipelineState {
  stages: Record<string, StageState>;
  reports: ReportMap;
  decision: { signal: string; decision: string } | null;
}

export const emptyPipelineState = (): PipelineState => ({
  stages: Object.fromEntries(
    PIPELINE_STAGES.map((name) => [name, "pending"] as const),
  ) as Record<string, StageState>,
  reports: {},
  decision: null,
});

/** Apply one parsed event; returns a NEW state (immutably, for React). */
export function applyEvent(state: PipelineState, event: RunEvent): PipelineState {
  if (event.type === "agent_status") {
    // Unknown agent names (future backend stages) are appended on arrival.
    return {
      ...state,
      stages: { ...state.stages, [event.agent]: event.status },
    };
  }

  if (event.type === "report") {
    return {
      ...state,
      reports: { ...state.reports, [event.key]: event.content },
    };
  }

  return {
    ...state,
    decision: { signal: event.signal, decision: event.decision },
  };
}

export function applyEnvelope(state: PipelineState, envelope: EventEnvelope): PipelineState {
  return applyEvent(state, envelope.event);
}

/** Names in display order: canonical stages first, then any extras in arrival order. */
export function stageNames(state: PipelineState): string[] {
  const extras = Object.keys(state.stages).filter(
    (name) => !PIPELINE_STAGES.includes(name),
  );
  return [...PIPELINE_STAGES, ...extras];
}

/** Index of the currently active (in_progress) stage in display order, or -1. */
export function activeStageIndex(state: PipelineState): number {
  const names = stageNames(state);
  for (let i = names.length - 1; i >= 0; i--) {
    if (state.stages[names[i]] === "in_progress") return i;
  }
  return -1;
}

export const REPORT_LABELS: Record<ReportKey, string> = {
  market_report: "Market Report",
  sentiment_report: "Sentiment Report",
  news_report: "News Report",
  fundamentals_report: "Fundamentals Report",
};

export const REPORT_ORDER: readonly ReportKey[] = [
  "market_report",
  "sentiment_report",
  "news_report",
  "fundamentals_report",
];
