import type { ReportMap } from "../state/pipeline";
import { REPORT_LABELS, REPORT_ORDER } from "../state/pipeline";
import { SafeMarkdown } from "./SafeMarkdown";

/**
 * Streaming analyst reports in fixed canonical order; sections appear as
 * their first report event arrives. Content is untrusted LLM text and is
 * rendered through SafeMarkdown (React escaping, no raw HTML).
 */
export function ReportSections({ reports }: { reports: ReportMap }) {
  const present = REPORT_ORDER.filter((key) => reports[key] !== undefined);
  if (present.length === 0) {
    return (
      <p className="text-sm text-slate-500" data-testid="reports-empty">
        Analyst reports will stream in here as the pipeline progresses.
      </p>
    );
  }
  return (
    <div className="space-y-4">
      {present.map((key) => (
        <section
          key={key}
          aria-label={REPORT_LABELS[key]}
          className="rounded-lg border border-terminal-border bg-terminal-panel p-4"
          data-testid={`report-${key}`}
        >
          <h3 className="numeric mb-2 text-xs font-semibold uppercase tracking-widest text-terminal-muted">
            {REPORT_LABELS[key]}
          </h3>
          <SafeMarkdown text={reports[key] ?? ""} />
        </section>
      ))}
    </div>
  );
}
