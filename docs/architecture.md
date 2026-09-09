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
├── interface.py      # InstructionProvider protocol — the ONLY contract the engine knows
├── adapters/
│   ├── direct.py     # Default: plain TradingAgentsGraph, no custom instructions
│   └── ta_plugins.py # Optional: plugin_scope + prompt_prefix_plugin (guarded import)
└── factory.py        # Chooses adapter; falls back to direct if ta_plugins unavailable
```

- The run engine consumes a `GraphRunner` built by the selected `InstructionProvider`
- If `ta_plugins` is missing/broken, the factory falls back to `direct` — GUI still runs

## Backend core (in development)

- **FastAPI + SSE**: REST for control, Server-Sent Events for live run streams
- **Run engine**: consumes `TradingAgentsGraph.stream()` state snapshots; maps graph state → UI events
- **Job manager**: one worker per analysis run; per-run event log with **fan-out** (multiple browser tabs share one run safely) and **replay** (late joiners get history)
- **Offline-first testing**: tests inject a stub graph factory — zero network, zero API keys

## Decision log

See `docs/decisions/` (ADR format).
