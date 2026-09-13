import { useMemo, useState, type FormEvent } from "react";
import { ApiError } from "../api/client";
import type { AssetType, Depth, Provider, RunConfig } from "../api/types";
import { ASSET_TYPES, DEPTHS, PROVIDERS } from "../api/types";
import {
  MAX_INSTRUCTIONS,
  formIsValid,
  instructionsEnabled,
  toRunConfig,
  validateForm,
  type FormErrors,
  type FormValues,
} from "../state/validation";

const LABEL = "block text-xs font-medium uppercase tracking-wider text-terminal-muted mb-1.5";
const INPUT =
  "w-full rounded border border-terminal-border bg-terminal-bg px-3 py-2 text-sm text-terminal-text " +
  "placeholder:text-slate-600 focus:border-terminal-accent focus:outline-none focus:ring-1 focus:ring-terminal-accent/40 disabled:opacity-40";
const DEPTH_HELP: Record<Depth, string> = {
  fast: "Quick scan: no debates, fast models, fewer sources.",
  standard: "Default: one debate round, balanced cost and rigor.",
  deep: "Most thorough: multi-round debates, all analysts.",
};

export function AnalyzeForm({
  onSubmit,
}: {
  onSubmit: (config: RunConfig) => Promise<void>;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [apiError, setApiError] = useState<{ status: number; detail: string } | null>(null);
  const [values, setValues] = useState<FormValues>({
    ticker: "",
    date: new Date().toISOString().slice(0, 10),
    asset_type: "stock",
    provider: "direct",
    depth: "standard",
    instructions: "",
  });
  const [touched, setTouched] = useState(false);

  const errors: FormErrors = useMemo(() => validateForm(values), [values]);
  const valid = useMemo(() => formIsValid(values), [values]);
  const instrEnabled = instructionsEnabled(values.provider);
  const instrLen = values.instructions.length;
  const overCap = instrLen > MAX_INSTRUCTIONS;

  const set = (key: keyof FormValues, value: FormValues[keyof FormValues]) =>
    setValues((v) => ({ ...v, [key]: value }));

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setTouched(true);
    setApiError(null);
    if (!valid || submitting) return;
    setSubmitting(true);
    try {
      await onSubmit(toRunConfig(values));
    } catch (err) {
      if (err instanceof ApiError) {
        setApiError({ status: err.status, detail: err.detail });
      } else {
        setApiError({ status: 0, detail: "Network error — could not reach the API." });
      }
    } finally {
      setSubmitting(false);
    }
  };

  const showTickerError = touched && errors.ticker;
  const showDateError = touched && errors.date;
  const showInstrError = touched && errors.instructions;

  return (
    <form
      onSubmit={handleSubmit}
      className="rounded-lg border border-terminal-border bg-terminal-panel p-5"
      aria-label="New analysis"
      noValidate
    >
      <h2 className="numeric mb-4 text-sm font-semibold uppercase tracking-widest text-emerald-400">
        New Analysis
      </h2>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <label htmlFor="ticker" className={LABEL}>
            Ticker
          </label>
          <input
            id="ticker"
            type="text"
            className={`numeric ${INPUT} ${showTickerError ? "border-red-500/60" : ""}`}
            placeholder="NVDA"
            value={values.ticker}
            maxLength={16}
            autoComplete="off"
            onChange={(e) => set("ticker", e.target.value.toUpperCase())}
            aria-invalid={Boolean(showTickerError)}
          />
          {showTickerError && <p className="mt-1 text-xs text-red-400">{errors.ticker}</p>}
        </div>

        <div>
          <label htmlFor="date" className={LABEL}>
            Analysis date
          </label>
          <input
            id="date"
            type="date"
            className={`numeric ${INPUT} ${showDateError ? "border-red-500/60" : ""}`}
            value={values.date}
            onChange={(e) => set("date", e.target.value)}
            aria-invalid={Boolean(showDateError)}
          />
          {showDateError && <p className="mt-1 text-xs text-red-400">{errors.date}</p>}
        </div>

        <div>
          <label htmlFor="asset_type" className={LABEL}>
            Asset type
          </label>
          <select
            id="asset_type"
            className={INPUT}
            value={values.asset_type}
            onChange={(e) => set("asset_type", e.target.value as AssetType)}
          >
            {ASSET_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label htmlFor="provider" className={LABEL}>
            Provider
          </label>
          <select
            id="provider"
            className={INPUT}
            value={values.provider}
            onChange={(e) => set("provider", e.target.value as Provider)}
          >
            {PROVIDERS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="mt-4">
        <span className={LABEL}>Analysis depth</span>
        <div className="grid grid-cols-3 gap-2" role="group" aria-label="Analysis depth">
          {DEPTHS.map((d) => {
            const active = values.depth === d;
            return (
              <button
                key={d}
                type="button"
                data-testid={`depth-${d}`}
                aria-pressed={active}
                onClick={() => set("depth", d)}
                className={`numeric rounded border px-3 py-2 text-xs font-semibold uppercase tracking-wider transition ${
                  active
                    ? "border-terminal-accent bg-terminal-accent/15 text-terminal-accent"
                    : "border-terminal-border bg-terminal-bg text-terminal-muted hover:border-terminal-accent/50 hover:text-terminal-text"
                } disabled:cursor-not-allowed disabled:opacity-40`}
                disabled={submitting}
              >
                {d}
              </button>
            );
          })}
        </div>
        <p className="mt-1.5 text-xs text-slate-500" data-testid="depth-help">
          {DEPTH_HELP[values.depth]}
        </p>
      </div>

      <div className="mt-4">
        <div className="mb-1.5 flex items-baseline justify-between">
          <label htmlFor="instructions" className={LABEL}>
            Custom instructions <span className="normal-case text-slate-600">(ta_plugins only)</span>
          </label>
          <span
            className={`numeric text-xs ${overCap ? "text-red-400 font-semibold" : instrLen > MAX_INSTRUCTIONS * 0.9 ? "text-amber-400" : "text-slate-500"}`}
            data-testid="instructions-counter"
          >
            {instrLen}/{MAX_INSTRUCTIONS}
          </span>
        </div>
        <textarea
          id="instructions"
          rows={5}
          className={`${INPUT} resize-y ${showInstrError || overCap ? "border-red-500/60" : ""}`}
          placeholder="Optional guidance injected into the agents' prompts..."
          value={values.instructions}
          disabled={!instrEnabled}
          onChange={(e) => set("instructions", e.target.value)}
          aria-invalid={Boolean(showInstrError) || overCap}
        />
        {!instrEnabled && (
          <p className="mt-1 text-xs text-slate-600">
            Switch provider to <span className="numeric">ta_plugins</span> to attach custom instructions.
          </p>
        )}
        {(showInstrError || overCap) && (
          <p className="mt-1 text-xs text-red-400" data-testid="instructions-error">
            {errors.instructions ?? `Instructions exceed ${MAX_INSTRUCTIONS} characters.`}
          </p>
        )}
      </div>

      {apiError && (
        <div
          role="alert"
          data-testid="api-error"
          className="mt-4 rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-300"
        >
          {apiError.status === 422
            ? `Request rejected (${apiError.status}): ${apiError.detail}`
            : apiError.status === 429
              ? `Capacity reached (${apiError.status}): ${apiError.detail} — try again shortly.`
              : `API error ${apiError.status}: ${apiError.detail}`}
        </div>
      )}

      <button
        type="submit"
        disabled={submitting || !valid || overCap}
        className="numeric mt-5 w-full rounded bg-terminal-accent/90 px-4 py-2.5 text-sm font-semibold uppercase tracking-wider text-slate-950 transition hover:bg-terminal-accent disabled:cursor-not-allowed disabled:opacity-40"
        data-testid="submit-run"
      >
        {submitting ? "Starting…" : "Run Analysis"}
      </button>
    </form>
  );
}
