import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "../api/client";
import type { JobStatus } from "../api/types";
import { RunStatusBadge } from "../components/Badges";

function formatCreated(ts: number): string {
  try {
    return new Date(ts * 1000).toLocaleString();
  } catch {
    return String(ts);
  }
}

export function RunsList({
  onOpenRun,
  reloadToken,
}: {
  onOpenRun: (jobId: string) => void;
  reloadToken: number;
}) {
  const [runs, setRuns] = useState<JobStatus[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const list = await api.listRuns();
      setRuns(list);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not load runs.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, reloadToken]);

  if (error) {
    return (
      <p className="numeric rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300" data-testid="runs-error">
        {error}
      </p>
    );
  }

  if (runs === null) {
    return <p className="text-sm text-slate-500">Loading runs…</p>;
  }

  if (runs.length === 0) {
    return (
      <p className="text-sm text-slate-500" data-testid="runs-empty">
        No runs yet — start an analysis above.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto" data-testid="runs-table">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="numeric text-xs uppercase tracking-wider text-terminal-muted">
            <th className="px-3 py-2">Ticker</th>
            <th className="px-3 py-2">Date</th>
            <th className="px-3 py-2">Asset</th>
            <th className="px-3 py-2">Provider</th>
            <th className="px-3 py-2">Status</th>
            <th className="px-3 py-2">Created</th>
            <th className="px-3 py-2">Events</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr
              key={run.job_id}
              onClick={() => onOpenRun(run.job_id)}
              className="cursor-pointer border-t border-terminal-border transition hover:bg-slate-800/40"
              data-testid={`run-row-${run.job_id}`}
            >
              <td className="numeric px-3 py-2 font-semibold text-terminal-text">{run.ticker}</td>
              <td className="numeric px-3 py-2 text-slate-400">{run.date}</td>
              <td className="numeric px-3 py-2 text-slate-400">{run.asset_type}</td>
              <td className="numeric px-3 py-2 text-slate-400">
                {run.provider}
                {run.effective_provider && run.effective_provider !== run.provider && (
                  <span className="text-amber-400"> → {run.effective_provider}</span>
                )}
              </td>
              <td className="px-3 py-2">
                <RunStatusBadge status={run.status} />
              </td>
              <td className="numeric px-3 py-2 text-slate-500">{formatCreated(run.created_at)}</td>
              <td className="numeric px-3 py-2 text-slate-400">{run.event_count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
