import { SafeMarkdown } from "./SafeMarkdown";

/*
 * Final decision card. Signal is provider-controlled but untrusted text:
 * styling keys off known values, everything renders via React escaping.
 */

function signalClass(signal: string): string {
  const s = signal.trim().toUpperCase();
  if (s.includes("REVIEW"))
    return "bg-amber-500/15 text-amber-300 border-amber-500/40";
  if (s === "BUY" || s === "LONG")
    return "bg-emerald-500/15 text-emerald-300 border-emerald-500/40";
  if (s === "SELL" || s === "SHORT")
    return "bg-red-500/15 text-red-300 border-red-500/40";
  if (s === "HOLD" || s === "NEUTRAL")
    return "bg-slate-600/30 text-slate-300 border-slate-500/40";
  return "bg-sky-500/15 text-sky-300 border-sky-500/40";
}

export function DecisionCard({
  signal,
  decision,
}: {
  signal: string;
  decision: string;
}) {
  return (
    <section
      aria-label="Final decision"
      className="rounded-lg border border-terminal-border bg-terminal-panel p-5"
      data-testid="decision-card"
    >
      <header className="mb-3 flex items-center gap-3">
        <h2 className="numeric text-xs font-semibold uppercase tracking-widest text-terminal-muted">
          Final Decision
        </h2>
        <span
          data-testid="decision-signal"
          className={`numeric rounded border px-2.5 py-1 text-sm font-bold uppercase tracking-wider ${signalClass(signal)}`}
        >
          {signal}
        </span>
      </header>
      {decision.trim().length > 0 ? (
        <SafeMarkdown text={decision} />
      ) : (
        <p className="text-sm text-slate-500">No decision text provided.</p>
      )}
    </section>
  );
}
