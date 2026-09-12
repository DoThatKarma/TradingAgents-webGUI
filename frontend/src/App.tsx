import { useCallback, useState } from "react";
import type { RunConfig } from "./api/types";
import { api } from "./api/client";
import { AnalyzeForm } from "./views/AnalyzeForm";
import { RunView } from "./views/RunView";
import { RunsList } from "./views/RunsList";

interface SelectedRun {
  jobId: string;
  returnTo: number;
}

export default function App() {
  const [selected, setSelected] = useState<SelectedRun | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  const handleSubmitRun = useCallback(
    async (config: RunConfig) => {
      const created = await api.startRun(config);
      // 202 accepted: clear the form's pending state by bumping reload and
      // opening the run view for the new job.
      setReloadToken((t) => t + 1);
      setSelected({ jobId: created.job_id, returnTo: 0 });
    },
    [],
  );

  const handleBack = useCallback(() => {
    setSelected((s) => (s === null ? null : { ...s, jobId: "" }));
  }, []);

  if (selected !== null && selected.jobId !== "") {
    return (
      <main className="mx-auto max-w-6xl px-4 py-6">
        <RunView jobId={selected.jobId} onBack={handleBack} />
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-6xl px-4 py-6">
      <header className="mb-6 flex items-center justify-between border-b border-terminal-border pb-4">
        <h1 className="numeric text-lg font-bold tracking-widest text-terminal-text">
          TRADINGAGENTS <span className="text-terminal-accent">TERMINAL</span>
        </h1>
        <span className="numeric text-xs text-slate-600">multi-agent stock analysis</span>
      </header>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[420px_1fr]">
        <div>
          <AnalyzeForm onSubmit={handleSubmitRun} />
        </div>
        <section aria-label="Recent runs">
          <h2 className="numeric mb-3 text-xs font-semibold uppercase tracking-widest text-terminal-muted">
            Recent Runs
          </h2>
          <RunsList onOpenRun={(jobId) => setSelected({ jobId, returnTo: 0 })} reloadToken={reloadToken} />
        </section>
      </div>
    </main>
  );
}
