import { useCallback, useEffect, useState } from "react";
import { ApiError, api, type ConnectionPhase } from "../api/client";
import { describeJobError } from "../api/errors";
import type { JobStatus } from "../api/types";
import {
  applyEnvelope,
  emptyPipelineState,
  type PipelineState,
} from "../state/pipeline";
import { ConnectionBadge, RunStatusBadge } from "../components/Badges";
import { DecisionCard } from "../components/DecisionCard";
import { PipelineStages } from "../components/PipelineStages";
import { ReportSections } from "../components/ReportSection";

const TERMINAL = ["completed", "cancelled", "failed"];

export function RunView({ jobId, onBack }: { jobId: string; onBack: () => void }) {
  const [status, setStatus] = useState<JobStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [pipeline, setPipeline] = useState<PipelineState>(emptyPipelineState);
  const [phase, setPhase] = useState<ConnectionPhase | null>(null);
  const [cancelNote, setCancelNote] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);

  const refreshStatus = useCallback(async () => {
    try {
      const s = await api.getStatus(jobId);
      setStatus(s);
    } catch (err) {
      setStatusError(err instanceof ApiError ? err.detail : "Could not load run status.");
    }
  }, [jobId]);

  useEffect(() => {
    void refreshStatus();

    // Replay from seq 0 — safe for late joiners and reconnects (server replays
    // retained history, then pushes live events until terminal).
    const close = api.openEventStream(jobId, {
      onEvent: (envelope) => {
        // Functional update: burst/replay events must all apply, not only the last.
        setPipeline((prev) => applyEnvelope(prev, envelope));
      },
      onPhase: (p) => setPhase(p),
      onDone: () => {
        void refreshStatus();
      },
      onError: () => {
        void refreshStatus();
      },
    });

    return close;
  }, [jobId, refreshStatus]);

  const handleCancel = async () => {
    if (cancelling) return;
    setCancelling(true);
    setCancelNote(null);
    try {
      const result = await api.cancelRun(jobId);
      setCancelNote(
        result.cancelled
          ? "Cancellation requested — the run stops between agent steps."
          : "Run already finished; entry evicted.",
      );
      await refreshStatus();
    } catch (err) {
      setCancelNote(
        err instanceof ApiError
          ? `Cancel failed (${err.status}): ${err.detail}`
          : "Cancel failed — network error.",
      );
    } finally {
      setCancelling(false);
    }
  };

  const terminal = status !== null && TERMINAL.includes(status.status);
  const decisionReceived = pipeline.decision !== null;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <button
          type="button"
          onClick={onBack}
          className="numeric rounded border border-terminal-border px-3 py-1.5 text-xs uppercase tracking-wider text-slate-400 transition hover:border-terminal-accent hover:text-terminal-text"
        >
          ← Back
        </button>
        <ConnectionBadge phase={phase} />
      </div>

      <section className="rounded-lg border border-terminal-border bg-terminal-panel p-5">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="numeric text-2xl font-bold tracking-wide text-terminal-text">
            {status?.ticker ?? "…"}
          </h1>
          {status && <RunStatusBadge status={status.status} />}
          {status && (
            <span className="numeric text-xs text-slate-500">
              {status.ticker} · {status.date} · {status.asset_type} · provider {status.provider}
              {status.effective_provider && status.effective_provider !== status.provider && (
                <span className="text-amber-400"> (effective: {status.effective_provider})</span>
              )}
            </span>
          )}
          {status?.has_instructions && (
            <span className="rounded border border-sky-500/40 bg-sky-500/10 px-2 py-0.5 text-xs text-sky-300">
              custom instructions
            </span>
          )}
        </div>
        {status?.error && (
          <p className="numeric mt-2 text-sm text-red-400" data-testid="run-error">
            Error: {describeJobError(status.error)}
          </p>
        )}
        {statusError && (
          <p className="numeric mt-2 text-sm text-red-400" data-testid="status-error">
            {statusError}
          </p>
        )}
        {!terminal && (
          <button
            type="button"
            onClick={handleCancel}
            disabled={cancelling}
            className="numeric mt-4 rounded border border-red-500/50 bg-red-500/10 px-3 py-1.5 text-xs font-semibold uppercase tracking-wider text-red-300 transition hover:bg-red-500/20 disabled:opacity-40"
            data-testid="cancel-run"
          >
            {cancelling ? "Cancelling…" : "Cancel run"}
          </button>
        )}
        {cancelNote && <p className="mt-2 text-xs text-amber-300">{cancelNote}</p>}
      </section>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[280px_1fr]">
        <section
          aria-label="Pipeline"
          className="rounded-lg border border-terminal-border bg-terminal-panel p-4"
        >
          <h2 className="numeric mb-3 text-xs font-semibold uppercase tracking-widest text-terminal-muted">
            Pipeline
          </h2>
          <PipelineStages state={pipeline} />
        </section>

        <div className="space-y-4">
          {pipeline.decision && (
            <DecisionCard signal={pipeline.decision.signal} decision={pipeline.decision.decision} />
          )}
          {terminal && !decisionReceived && status?.status === "failed" && (
            <p className="text-sm text-slate-500" data-testid="no-decision">
              Run failed before a decision was reached.
            </p>
          )}
          <ReportSections reports={pipeline.reports} />
        </div>
      </div>
    </div>
  );
}
