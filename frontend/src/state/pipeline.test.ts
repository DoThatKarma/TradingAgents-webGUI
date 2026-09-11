import { describe, expect, it } from "vitest";
import {
  applyEnvelope,
  applyEvent,
  emptyPipelineState,
  stageNames,
} from "./pipeline";
import type { EventEnvelope } from "../api/types";

const env = (seq: number, event: EventEnvelope["event"]): EventEnvelope => ({
  seq,
  ts: 0,
  event,
});

describe("pipeline event→state mapping", () => {
  it("starts all canonical stages as pending in server order", () => {
    const state = emptyPipelineState();
    expect(stageNames(state)).toEqual([
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
    ]);
    for (const name of stageNames(state)) {
      expect(state.stages[name]).toBe("pending");
    }
  });

  it("marks stages in_progress then completed from agent_status events", () => {
    let state = emptyPipelineState();
    state = applyEvent(state, {
      type: "agent_status",
      agent: "Market Analyst",
      status: "in_progress",
    });
    expect(state.stages["Market Analyst"]).toBe("in_progress");
    state = applyEvent(state, {
      type: "agent_status",
      agent: "Market Analyst",
      status: "completed",
    });
    expect(state.stages["Market Analyst"]).toBe("completed");
  });

  it("stores report content by key", () => {
    let state = emptyPipelineState();
    state = applyEvent(state, {
      type: "report",
      key: "market_report",
      content: "# Market Analysis\nBullish momentum.",
    });
    expect(state.reports.market_report).toContain("Bullish momentum.");
  });

  it("sets the decision from a decision event incl. REVIEW signals", () => {
    let state = emptyPipelineState();
    state = applyEnvelope(
      state,
      env(9, { type: "decision", signal: "REVIEW: mixed signals", decision: "Final trade decision text." }),
    );
    expect(state.decision).toEqual({
      signal: "REVIEW: mixed signals",
      decision: "Final trade decision text.",
    });
  });

  it("applies full SSE envelope stream in seq order (server scenario)", () => {
    let state = emptyPipelineState();
    const stream: EventEnvelope[] = [
      env(1, { type: "agent_status", agent: "Market Analyst", status: "in_progress" }),
      env(2, { type: "report", key: "market_report", content: "report v1" }),
      env(3, { type: "agent_status", agent: "Market Analyst", status: "completed" }),
      env(4, { type: "agent_status", agent: "Sentiment Analyst", status: "in_progress" }),
      env(5, { type: "report", key: "market_report", content: "report v2" }),
      env(6, { type: "decision", signal: "BUY", decision: "go long" }),
    ];
    for (const e of stream) state = applyEnvelope(state, e);
    expect(state.stages["Market Analyst"]).toBe("completed");
    expect(state.stages["Sentiment Analyst"]).toBe("in_progress");
    expect(state.reports.market_report).toBe("report v2");
    expect(state.decision?.signal).toBe("BUY");
  });

  it("appends unknown future stage names after canonical stages", () => {
    let state = emptyPipelineState();
    state = applyEvent(state, {
      type: "agent_status",
      agent: "Mystery Analyst",
      status: "in_progress",
    });
    const names = stageNames(state);
    expect(names[names.length - 1]).toBe("Mystery Analyst");
    expect(state.stages["Mystery Analyst"]).toBe("in_progress");
  });
});
