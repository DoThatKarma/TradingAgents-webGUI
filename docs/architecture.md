# Architecture

## Repos overview

| Repo | Role |
| --- | --- |
| `TauricResearch/TradingAgents` (upstream) | Consumed as pip dependency, never modified |
| `DoThatKarma/TradingAgentsPlugin` | Optional plugin layer (`ta_plugins`) — powers custom instructions |
| **`DoThatKarma/TradingAgents-webGUI`** (this repo) | Standalone web GUI |

## Decoupling contract (core requirement)

The GUI **must function without the plugin framework**. Therefore:

```
server/app/instructions/
├── interface.py            # InstructionProvider protocol — the ONLY contract the engine knows
├── adapters/
│   ├── direct.py           # Default: plain TradingAgentsGraph, no custom instructions
│   └── ta_plugins_adapter.py  # Optional: plugin_scope + prompt_prefix_plugin (guarded import)
└── factory.py              # Chooses adapter; falls back to direct if ta_plugins unavailable
```

- Graph config resolution (LLM provider/models, `selected_analysts`) is shared
  by both adapters via `graph_config.py` — every run gets the same environment
  layering (GUI baseline < upstream `TRADINGAGENTS_*` < `TA_WEBGUI_*`)
- Analysis depth (`fast`/`standard`/`deep`, ADR 0007) rides the same path:
  on preset-mapped keys the precedence becomes depth preset > `TA_WEBGUI_*` >
  `TRADINGAGENTS_*` > GUI baseline; unmapped keys keep the layering above
- The run engine consumes a `GraphRunner` built by the selected `InstructionProvider`
- If `ta_plugins` is missing/broken:
  - without custom instructions the factory falls back to `direct` (with a warning) — GUI still runs
  - **with** custom instructions it raises `ProviderUnavailableError` — silently dropping
    user-authored instructions would run a different analysis than requested
- The resolved (post-fallback) provider is recorded per job as `effective_provider`
  and exposed in the status API

## Backend core (in development)

- **FastAPI + SSE**: REST for control, Server-Sent Events for live run streams
  (keep-alive pings enabled; replay via `Last-Event-ID` header or `cursor` query
  parameter — a well-formed header wins)
- **Run engine**: consumes `TradingAgentsGraph.stream()` state snapshots; maps graph state → UI events
- **Job manager**: one worker per analysis run; per-run event log with **fan-out** (multiple browser tabs share one run safely) and **replay** (late joiners get history)
- **Offline-first testing**: tests inject a stub graph factory — zero network, zero API keys

## Backend hardening

Resource limits protecting the single-process backend (all configurable via
`JobManager` constructor arguments):

- **Bounded concurrency**: at most `max_concurrent` (default 3) runs execute at
  once; up to `max_queued` (default 50) more wait in line. Beyond that,
  `POST /api/runs` answers `429` with a `Retry-After` header.
- **Event-log cap**: per-job event logs keep the last `max_events` (default
  10 000) envelopes; `seq` numbers stay monotonic, but replay beyond the trim
  point is unavailable.
- **Job eviction**: terminal jobs are evicted after `job_ttl_seconds`
  (default 3600 s) by a sweep on manager entry points; `DELETE /api/runs/{id}`
  evicts terminal runs on demand and refuses (cancels instead) non-terminal ones.
- **Subscriber cap**: at most `max_subscribers` (default 10) concurrent SSE
  subscribers per run; excess connections get `429` + `Retry-After`.
- **Provider-lock timeout**: runner construction is serialized by a process-global
  provider lock (`ta_plugins` patches class-level factories); a job that cannot
  acquire it within `provider_lock_timeout` (default 120 s) fails with the
  `ProviderLockTimeout` error category instead of blocking a worker forever.
- **Error hygiene**: job errors surface only a short category (`RuntimeError`,
  `Cancelled`, …); details and tracebacks go to the server log, never to clients.
  Validation failures answer a static `422` with no echo of the rejected input;
  free-text `instructions` reject control characters (a shared rule enforced at
  both the API boundary and `JobManager.submit_run`) while allowing
  `\n`/`\r`/`\t` for prompt formatting.

### Deployment protection

Modest, layered app-level protection per
[ADR 0005](decisions/0005-app-level-protection.md): the reverse proxy owns TLS
termination and rate limiting; the app optionally gates /api/* behind a static
bearer token (TA_WEBGUI_API_TOKEN, GET /api/health stays open), adds four
security headers to every response, and keeps CORS disabled by default
(same-origin behind the proxy). Without the token set, behavior is identical
to local development.

## Decision log

See `docs/decisions/` (ADR format).
