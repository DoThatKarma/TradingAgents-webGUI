// Typed API client — contract owned by server/app/api/app.py.
// SSE is implemented over fetch() streaming: native EventSource cannot send
// an Authorization header; reconnects resend the cursor for server-side replay.

import { parseEnvelope } from "./guards";
import type { DeleteRunResult, HealthInfo, JobStatus, RunConfig, RunCreated } from "./types";

export type ConnectionPhase = "live" | "reconnecting" | "closed";

export interface StreamHandlers {
  onEvent: (envelope: import("./types").EventEnvelope) => void;
  onPhase?: (phase: ConnectionPhase) => void;
  /** Resolves-phase end: stream finished cleanly (server closed after terminal event). */
  onDone?: () => void;
  /** Fatal after exhausting reconnect attempts (or 404/403-style terminal errors). */
  onError?: (err: Error) => void;
}

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly retryAfter: number | null;

  constructor(status: number, detail: string, retryAfter: number | null = null) {
    super(`API ${status}: ${detail}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.retryAfter = retryAfter;
  }
}

const MAX_RECONNECT_ATTEMPTS = 8;
const BASE_BACKOFF_MS = 500;
const MAX_BACKOFF_MS = 15_000;
const MAX_SSE_BUFFER_BYTES = 1_000_000;
const TERMINAL_RUN_STATUSES: readonly string[] = ["completed", "cancelled", "failed"];

function abortError(): Error {
  const err = new Error("Aborted");
  err.name = "AbortError";
  return err;
}

export class ApiClient {
  private readonly base: string;
  private readonly token: string | null;

  constructor(base = import.meta.env.VITE_API_BASE ?? "/api") {
    this.base = base.replace(/\/$/, "");
    const token = import.meta.env.VITE_API_TOKEN;
    this.token = token && token.trim().length > 0 ? token.trim() : null;
  }

  private headers(extra?: Record<string, string>): Record<string, string> {
    const headers: Record<string, string> = { ...extra };
    if (this.token) headers["Authorization"] = `Bearer ${this.token}`;
    return headers;
  }

  private async readError(res: Response): Promise<ApiError> {
    let detail = res.statusText || "request failed";
    try {
      const body: unknown = await res.json();
      if (body && typeof body === "object" && "detail" in body) {
        const d = (body as { detail?: unknown }).detail;
        if (typeof d === "string") detail = d;
      }
    } catch {
      // non-JSON error body — keep statusText fallback
    }
    const retryAfterHeader = res.headers.get("Retry-After");
    const retryAfter = retryAfterHeader !== null && /^\d+$/.test(retryAfterHeader)
      ? Number(retryAfterHeader)
      : null;
    return new ApiError(res.status, detail, retryAfter);
  }

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const res = await fetch(`${this.base}${path}`, {
      ...init,
      headers: this.headers(
        init?.body !== undefined ? { "Content-Type": "application/json" } : undefined,
      ),
    });
    if (!res.ok) throw await this.readError(res);
    return (await res.json()) as T;
  }

  /** POST /api/runs → 202 {job_id} | 422 {detail} | 429 (Retry-After). */
  async startRun(config: RunConfig): Promise<RunCreated> {
    return this.request<RunCreated>("/runs", {
      method: "POST",
      body: JSON.stringify(config),
    });
  }

  /** GET /api/runs/{id} → status snapshot incl. effective_provider. */
  async getStatus(jobId: string): Promise<JobStatus> {
    return this.request<JobStatus>(`/runs/${encodeURIComponent(jobId)}`);
  }

  /** GET /api/runs → all non-evicted runs, creation order. */
  async listRuns(): Promise<JobStatus[]> {
    return this.request<JobStatus[]>("/runs");
  }

  /** GET /api/health → liveness probe. */
  async health(): Promise<HealthInfo> {
    return this.request<HealthInfo>("/health");
  }

  /**
   * DELETE /api/runs/{id} — evicts a terminal run; cancels a non-terminal one.
   * Returns the typed result so the UI can report which action occurred.
   */
  async cancelRun(jobId: string): Promise<DeleteRunResult> {
    const res = await fetch(`${this.base}/runs/${encodeURIComponent(jobId)}`, {
      method: "DELETE",
      headers: this.headers(),
    });
    if (!res.ok) throw await this.readError(res);
    return (await res.json()) as DeleteRunResult;
  }

  /**
   * Subscribe to the SSE run-event stream with automatic reconnect.
   *
   * Replays from `cursor` (last seq seen); reconnects resend the cursor
   * query param with the last applied seq so missed events are replayed. The
   * backend ends
   * the stream by closing it once the run is terminal; a literal `DONE`
   * sentinel is tolerated defensively.
   */
  openEventStream(jobId: string, handlers: StreamHandlers, cursor = 0): () => void {
    const controller = new AbortController();
    let attempts = 0;
    let lastSeq = cursor;
    let stopped = false;

    const sleep = (ms: number, signal: AbortSignal) =>
      new Promise<void>((resolve, reject) => {
        const t = setTimeout(resolve, ms);
        signal.addEventListener(
          "abort",
          () => {
            clearTimeout(t);
            reject(abortError());
          },
          { once: true },
        );
      });

    const reconnectOrGiveUp = async (err?: unknown): Promise<boolean> => {
      attempts += 1;
      if (attempts > MAX_RECONNECT_ATTEMPTS) {
        handlers.onPhase?.("closed");
        handlers.onError?.(
          err instanceof Error ? err : new Error("stream connection failed"),
        );
        return false;
      }
      const retryAfter = err instanceof ApiError ? err.retryAfter : null;
      const backoff =
        retryAfter !== null
          ? Math.min(retryAfter * 1000, MAX_BACKOFF_MS)
          : Math.min(BASE_BACKOFF_MS * 2 ** (attempts - 1), MAX_BACKOFF_MS);
      handlers.onPhase?.("reconnecting");
      try {
        await sleep(backoff, controller.signal);
        return true;
      } catch {
        return false; // aborted during backoff
      }
    };

    const connect = async (): Promise<void> => {
      while (!stopped) {
        try {
          const res = await fetch(
            `${this.base}/runs/${encodeURIComponent(jobId)}/events?cursor=${lastSeq}`,
            { headers: this.headers(), signal: controller.signal },
          );
          if (res.status === 429) {
            // Subscriber cap / pool full: retryable, honors Retry-After.
            throw await this.readError(res);
          }
          if (!res.ok || !res.body) {
            const err = await this.readError(res);
            // Terminal client errors: retrying cannot fix auth or a deleted run.
            if (res.status === 401 || res.status === 403 || res.status === 404) {
              handlers.onPhase?.("closed");
              handlers.onError?.(err);
              return;
            }
            throw err;
          }

          attempts = 0;
          if (!stopped) handlers.onPhase?.("live");

          const reader = res.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";

          // Minimal SSE frame parser: data:/id:/event: lines, blank-line frames.
          const handleFrame = (frame: string) => {
            const lines = frame.split("\n");
            let data = "";
            let id: string | null = null;
            for (const line of lines) {
              if (line.startsWith("data:")) data += (data ? "\n" : "") + line.slice(5).trim();
              else if (line.startsWith("id:")) id = line.slice(3).trim();
              // ': ping' comments ignored
            }
            if (!data) return;
            if (data === "DONE") {
              stopped = true;
              handlers.onDone?.();
              return;
            }
            const envelope = parseEnvelope(data);
            if (envelope) {
              lastSeq = envelope.seq;
              handlers.onEvent(envelope);
            }
            if (id !== null) lastSeq = Math.max(lastSeq, Number(id) || lastSeq);
          };

          for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            // Normalize CRLF -> LF across the whole residual buffer: the server
            // (sse-starlette) frames with \r\n\r\n and a chunk boundary can
            // split the pair. Scan only after normalization.
            buffer += decoder.decode(value, { stream: true });
            buffer = buffer.replace(/\r\n/g, "\n");
            let idx: number;
            while ((idx = buffer.indexOf("\n\n")) !== -1) {
              handleFrame(buffer.slice(0, idx));
              buffer = buffer.slice(idx + 2);
            }
            if (buffer.length > MAX_SSE_BUFFER_BYTES) {
              // Framing-anomaly guard: reconnect instead of unbounded growth.
              reader.cancel().catch(() => {});
              throw new Error("SSE buffer overflow (unterminated frames)");
            }
          }

          // Server closed the stream: terminal close, or a proxy/network drop
          // mid-run. Ask the status endpoint which one it was.
          if (stopped) break;
          let terminal = false;
          try {
            const status = await this.getStatus(jobId);
            terminal = TERMINAL_RUN_STATUSES.includes(status.status);
          } catch {
            // Status check failed — treat as a dropped connection and retry.
          }
          if (terminal) {
            handlers.onPhase?.("closed");
            handlers.onDone?.();
            return;
          }
          if (!(await reconnectOrGiveUp())) return;
        } catch (err) {
          if (stopped || controller.signal.aborted) return;
          if (err instanceof Error && err.name === "AbortError") return;
          if (!(await reconnectOrGiveUp(err))) return;
        }
      }
    };

    void connect();
    return () => {
      stopped = true;
      controller.abort();
    };
  }
}

export const api = new ApiClient();
