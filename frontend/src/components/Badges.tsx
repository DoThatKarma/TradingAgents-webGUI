const STYLES: Record<string, string> = {
  queued: "bg-slate-700/40 text-slate-300 border-slate-600",
  running: "bg-sky-500/15 text-sky-300 border-sky-500/40 animate-pulse",
  completed: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
  cancelled: "bg-amber-500/15 text-amber-300 border-amber-500/40",
  failed: "bg-red-500/15 text-red-300 border-red-500/40",
};

export function RunStatusBadge({ status }: { status: string }) {
  const cls = STYLES[status] ?? STYLES.queued;
  return (
    <span
      className={`numeric inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium uppercase tracking-wider ${cls}`}
      data-status={status}
    >
      {status}
    </span>
  );
}

export function ConnectionBadge({ phase }: { phase: "live" | "reconnecting" | "closed" | null }) {
  if (!phase) return null;
  const map = {
    live: { cls: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40", dot: "bg-emerald-400", label: "LIVE" },
    reconnecting: { cls: "bg-amber-500/15 text-amber-300 border-amber-500/40", dot: "bg-amber-400 animate-pulse", label: "RECONNECTING" },
    closed: { cls: "bg-slate-700/40 text-slate-400 border-slate-600", dot: "bg-slate-500", label: "CLOSED" },
  } as const;
  const s = map[phase];
  return (
    <span
      className={`numeric inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-xs font-medium tracking-wider ${s.cls}`}
      data-connection={phase}
    >
      <span className={`inline-block h-1.5 w-1.5 rounded-full ${s.dot}`} />
      {s.label}
    </span>
  );
}
