import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClient, ApiError } from "../api/client";

// Offline tests: fetch is mocked; no server exists in the test environment.

const jsonOk = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

describe("ApiClient auth header", () => {
  it("attaches Authorization: Bearer when VITE_API_TOKEN is set", async () => {
    vi.stubEnv("VITE_API_TOKEN", "test-token-123");
    vi.stubEnv("VITE_API_BASE", "/api");
    const client = new ApiClient();
    fetchMock.mockResolvedValue(jsonOk({ status: "ok" }));

    await client.health();

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/health");
    const headers = init.headers as Record<string, string>;
    expect(headers["Authorization"]).toBe("Bearer test-token-123");
    vi.unstubAllEnvs();
  });

  it("sends no Authorization header when no token is configured", async () => {
    vi.stubEnv("VITE_API_TOKEN", "");
    const client = new ApiClient();
    fetchMock.mockResolvedValue(jsonOk({ status: "ok" }));

    await client.health();

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(headers["Authorization"]).toBeUndefined();
    vi.unstubAllEnvs();
  });

  it("attaches the bearer to POST /runs and SSE stream requests", async () => {
    vi.stubEnv("VITE_API_TOKEN", "tok");
    const client = new ApiClient();
    fetchMock.mockResolvedValueOnce(jsonOk({ job_id: "j1", status: "queued" }, 202));

    await client.startRun({
      ticker: "NVDA",
      date: "2024-05-10",
      asset_type: "stock",
      provider: "direct",
    });

    const [, postInit] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((postInit.headers as Record<string, string>)["Authorization"]).toBe("Bearer tok");
    expect((postInit.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    vi.unstubAllEnvs();
  });
});

describe("ApiClient error mapping", () => {
  it("surfaces static 422 detail and keeps status", async () => {
    vi.stubEnv("VITE_API_TOKEN", "");
    const client = new ApiClient();
    fetchMock.mockResolvedValue(jsonOk({ detail: "invalid run request" }, 422));

    await expect(
      client.startRun({
        ticker: "BAD!!",
        date: "2024-05-10",
        asset_type: "stock",
        provider: "direct",
      }),
    ).rejects.toMatchObject({ status: 422, detail: "invalid run request" });
    vi.unstubAllEnvs();
  });

  it("parses Retry-After on 429", async () => {
    vi.stubEnv("VITE_API_TOKEN", "");
    const client = new ApiClient();
    fetchMock.mockResolvedValue(
      jsonOk({ detail: "run capacity reached; retry later" }, 429, { "Retry-After": "5" }),
    );

    await expect(client.listRuns()).rejects.toMatchObject({
      status: 429,
      retryAfter: 5,
    });
    vi.unstubAllEnvs();
  });
});

describe("openEventStream", () => {
  const enc = new TextEncoder();

  const streamResponse = (chunks: string[], status = 200) =>
    new Response(
      new ReadableStream<Uint8Array>({
        start(controller) {
          for (const c of chunks) controller.enqueue(enc.encode(c));
          controller.close();
        },
      }),
      { status, headers: { "Content-Type": "text/event-stream" } },
    );

  const frame = (seq: number) =>
    `id: ${seq}\r\ndata: ${JSON.stringify({
      seq,
      ts: 1,
      event: { type: "agent_status", agent: "market_analyst", status: "in_progress" },
    })}\r\n\r\n`;

  it("parses CRLF frames even when a chunk splits mid-frame", async () => {
    vi.stubEnv("VITE_API_TOKEN", "");
    const client = new ApiClient();
    // Frame terminator split across chunks; ping comment must be ignored.
    fetchMock.mockResolvedValueOnce(
      streamResponse([frame(1).slice(0, -3), "\r\n\r\n", ": ping\r\n\r\n"]),
    );
    fetchMock.mockResolvedValueOnce(jsonOk({ status: "completed", job_id: "j1" }));

    const events: unknown[] = [];
    let done = false;
    const close = client.openEventStream("j1", {
      onEvent: (e) => events.push(e),
      onDone: () => {
        done = true;
      },
    });

    await vi.waitFor(() => expect(done).toBe(true));
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ seq: 1 });
    expect(fetchMock).toHaveBeenCalledTimes(2); // stream + terminal status check
    close();
    close();
        vi.unstubAllEnvs();
  });

  it("applies every frame in a single burst chunk", async () => {
    vi.stubEnv("VITE_API_TOKEN", "");
    const client = new ApiClient();
    fetchMock.mockResolvedValueOnce(streamResponse([frame(1) + frame(2) + frame(3)]));
    fetchMock.mockResolvedValueOnce(jsonOk({ status: "completed" }));

    const events: number[] = [];
    let done = false;
    const close = client.openEventStream("j1", {
      onEvent: (e) => events.push(e.seq),
      onDone: () => {
        done = true;
      },
    });

    await vi.waitFor(() => expect(done).toBe(true));
    expect(events).toEqual([1, 2, 3]);
    close();
        vi.unstubAllEnvs();
  });

  it("reconnects on clean non-terminal close and finishes on terminal (S1)", async () => {
    vi.stubEnv("VITE_API_TOKEN", "");
    const client = new ApiClient();
    fetchMock.mockResolvedValueOnce(streamResponse([frame(1)]));
    fetchMock.mockResolvedValueOnce(jsonOk({ status: "running" }));
    fetchMock.mockResolvedValueOnce(streamResponse([]));
    fetchMock.mockResolvedValueOnce(jsonOk({ status: "completed" }));

    const events: number[] = [];
    let done = false;
    const close = client.openEventStream("j1", {
      onEvent: (e) => events.push(e.seq),
      onDone: () => {
        done = true;
      },
    });

    await vi.waitFor(() => expect(done).toBe(true), { timeout: 5000 });
    expect(events).toEqual([1]);
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(4);
    close();
        vi.unstubAllEnvs();
  });

  it("fails fast on 404 without retrying (S2)", async () => {
    vi.stubEnv("VITE_API_TOKEN", "");
    const client = new ApiClient();
    fetchMock.mockResolvedValue(jsonOk({ detail: "unknown run" }, 404));

    const errors: ApiError[] = [];
    const close = client.openEventStream("missing", {
      onEvent: () => {},
      onError: (e) => {
        errors.push(e as ApiError);
      },
    });

    await vi.waitFor(() => expect(errors).toHaveLength(1));
    expect(errors[0]?.status).toBe(404);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    close();
        vi.unstubAllEnvs();
  });

  it("treats a DONE sentinel as terminal without a status roundtrip", async () => {
    vi.stubEnv("VITE_API_TOKEN", "");
    const client = new ApiClient();
    fetchMock.mockResolvedValueOnce(streamResponse(["data: DONE\r\n\r\n"]));

    let done = false;
    const close = client.openEventStream("j1", {
      onEvent: () => {},
      onDone: () => {
        done = true;
      },
    });

    await vi.waitFor(() => expect(done).toBe(true));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    close();
        vi.unstubAllEnvs();
  });
});

describe("downloadReport", () => {
  const markdownOk = (body: string, headers: Record<string, string> = {}) =>
    new Response(body, {
      status: 200,
      headers: { "Content-Type": "text/markdown; charset=utf-8", ...headers },
    });

  it("fetches the report endpoint with the bearer and parses the filename", async () => {
    vi.stubEnv("VITE_API_TOKEN", "tok");
    const client = new ApiClient();
    fetchMock.mockResolvedValue(
      markdownOk("# TradingAgents Analysis Report", {
        "Content-Disposition": 'attachment; filename="tradingagents-NVDA-2025-01-10.md"',
      }),
    );

    const { blob, filename } = await client.downloadReport("j1");

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/runs/j1/report");
    expect((init.headers as Record<string, string>)["Authorization"]).toBe("Bearer tok");
    expect(filename).toBe("tradingagents-NVDA-2025-01-10.md");
    expect(blob.type).toBe("text/markdown;charset=utf-8");
    expect(blob.size).toBe("# TradingAgents Analysis Report".length);
    vi.unstubAllEnvs();
  });

  it("falls back to a job-id filename without Content-Disposition", async () => {
    vi.stubEnv("VITE_API_TOKEN", "");
    const client = new ApiClient();
    fetchMock.mockResolvedValue(markdownOk("# report"));

    const { filename } = await client.downloadReport("job-9");

    expect(filename).toBe("tradingagents-job-9.md");
    vi.unstubAllEnvs();
  });

  it("maps 404 to ApiError with the static detail", async () => {
    vi.stubEnv("VITE_API_TOKEN", "");
    const client = new ApiClient();
    fetchMock.mockResolvedValue(jsonOk({ detail: "report not available" }, 404));

    await expect(client.downloadReport("j1")).rejects.toMatchObject({
      name: "ApiError",
      status: 404,
      detail: "report not available",
    });
    vi.unstubAllEnvs();
  });
});
