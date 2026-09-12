import type { PipelineState, StageState } from "../state/pipeline";
import { stageNames } from "../state/pipeline";

const ICONS: Record<StageState, string> = {
  pending: "○",
  in_progress: "◐",
  completed: "●",
};

const COLORS: Record<StageState, string> = {
  pending: "text-slate-600",
  in_progress: "text-sky-300 animate-pulse",
  completed: "text-emerald-400",
};

const GROUP_STARTS = new Set(["Bull Researcher", "Aggressive Analyst"]);

export function PipelineStages({ state }: { state: PipelineState }) {
  const names = stageNames(state);
  return (
    <ol className="space-y-1">
      {names.map((name) => {
        const stage = state.stages[name] ?? "pending";
        return (
          <li
            key={name}
            className={`flex items-center gap-2 rounded px-2 py-1 text-sm ${
              GROUP_STARTS.has(name) ? "mt-3 border-t border-terminal-border pt-3" : ""
            }`}
            data-stage={name}
            data-stage-state={stage}
          >
            <span aria-hidden className={`numeric text-xs ${COLORS[stage]}`}>
              {ICONS[stage]}
            </span>
            <span
              className={
                stage === "completed"
                  ? "text-slate-300"
                  : stage === "in_progress"
                    ? "text-terminal-text font-medium"
                    : "text-slate-500"
              }
            >
              {name}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
