# TradingAgents-webGUI

A professional browser GUI for the TradingAgents multi-agent stock analysis
framework: market, sentiment, news and fundamentals analysts → bull/bear
research debate → trader → risk debate → final decision, live in your browser.

When a run completes, a **Download report** button lets you save the full analysis as a Markdown file.

Built-in LLM defaults: **OpenRouter** with model **z-ai/glm-5.3-flash** — you
only need an OpenRouter API key (https://openrouter.ai/keys).

## Quickstart (3 steps)

Requires Python 3.11+ (https://www.python.org/downloads/).

1. **Unzip** this file and open a terminal in the `tradingagents-webgui`
   folder.

2. **Install** (one time, Python 3.11+):

   ```bash
   python3 -m venv .venv
   .venv/bin/python -m pip install -r requirements.lock
   ```

   ```bat
   python -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.lock
   ```

3. **Run** with your OpenRouter key:

   ```bash
   OPENROUTER_API_KEY=sk-or-... ./run.sh
   ```

   ```bat
   set OPENROUTER_API_KEY=sk-or-...
   run.bat
   ```

Then open **http://127.0.0.1:8000**. (The run scripts also install into
`.venv` automatically on first run if you skipped step 2. Stop with Ctrl+C.)

## Choosing analysis depth

The New Analysis form has a **depth** selector (default **standard**):

- **Fast** — quick scan: no research/risk debates, fast models for both
  roles, market + fundamentals analysts only, smaller news windows.
- **Standard** — the balanced default: one research debate round, all four
  analysts, your configured models. Behaves exactly like previous releases.
- **Deep** — most thorough: 3 research debate rounds, 2 risk debate rounds,
  all four analysts, high reasoning effort (where the provider supports it),
  larger news windows and a bigger step budget. Costs more tokens and time.

Depth settings beat `TA_WEBGUI_*` / `TRADINGAGENTS_*` environment variables
for the settings the preset controls; everything else still honours the
environment (see `docs/decisions/0007-depth-presets.md` for the exact table
and precedence rules). The chosen depth is shown as a badge on the run page.

## Where things live

- Data caches (reports, market data, memories): `~/.tradingagents` — delete it
  to reset (`C:\Users\<you>\.tradingagents` on Windows).
- Finished-run history (see *Run persistence* below): `data/runs/` in the
  application folder when started with the run scripts.
- Built frontend assets: `webui/`, served by the backend automatically.
- Server logs: the terminal you started the server from.

## Run persistence: finished runs survive restarts

When started with `run.sh` / `run.bat`, every **finished** run (completed,
failed, or cancelled) is saved as one JSON file under
`data/runs/jobs/<run id>.json`, and this history is restored automatically
on the next start — status, the run list, and the Markdown report download
all keep working after a restart or version switch. Restored runs are never
re-executed; deleting a run in the UI removes its file as well. Files
contain analysis content and run metadata (never API keys or tracebacks).

- **Override the location:** export `TA_WEBGUI_PERSIST_DIR=/some/dir` before
  launching (Windows: `set TA_WEBGUI_PERSIST_DIR=D:\some\dir`).
- **Disable persistence:** set `TA_WEBGUI_PERSIST_DIR` to an empty string
  (`export TA_WEBGUI_PERSIST_DIR=`). Without the variable — e.g. when
  starting the server manually with uvicorn — runs stay in memory only and
  are lost on restart, exactly as before.
- **What survives a restart:** finished runs and their reports. Runs that
  were still queued or running when the server stopped are gone (they are
  not persisted) — start them again.
- **Disk space:** one small JSON file per run; delete runs in the UI (or
  remove files) to reclaim space.

## Security: localhost by default, optional API token

The server binds to `127.0.0.1` — reachable only from your machine, no token
needed. If you expose the port beyond localhost, set `TA_WEBGUI_API_TOKEN`:
every API call except `/api/health` then requires
`Authorization: Bearer <token>`.

**Important:** the bundled web UI reads its token at *build* time
(`VITE_API_TOKEN`), so this packaged build ships **without** a token and has
no token prompt. A token-protected deployment requires rebuilding the
frontend from source with `VITE_API_TOKEN` baked in. For anything beyond
personal localhost use, run behind a reverse proxy with TLS (nginx/Caddy).

## .env & API keys

`export OPENROUTER_API_KEY=...` works, and so does a `.env` file: the upstream
framework auto-loads one from the working directory upward. `run.sh` starts
the server in `server/`, so a `.env` in the extracted root **or** in
`server/` both work:

```dotenv
OPENROUTER_API_KEY=sk-or-v1-...
# optional overrides (rarely needed):
# TRADINGAGENTS_LLM_PROVIDER=openai
# TRADINGAGENTS_QUICK_THINK_LLM=gpt-4o-mini
```

## Optional data keys

- `FRED_API_KEY` — free key at
  https://fred.stlouisfed.org/docs/api/api_key.html — enables macro-economic
  indicators. Without it, `Vendor fred not configured` /
  `Optional macro_data unavailable` log lines are expected and harmless (the
  run falls back to the next data vendor).
- Reddit RSS `429` backoff messages are upstream's built-in rate-limit
  handling — harmless.

## Upstream

Built on [TradingAgents](https://github.com/TauricResearch/TradingAgents) by
Tauric Research. This GUI is an independent project — see `LICENSE`.
