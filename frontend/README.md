# TradingAgents Terminal — Frontend

Professional dark trading-terminal UI for the TradingAgents web backend: submit
stock analyses, watch the 12-stage multi-agent pipeline stream live over SSE,
and read the final decision and agent reports.

Stack: **Vite + React 19 + TypeScript (strict) + Tailwind CSS 4 + Vitest**.
No router dependency; view switching is minimal app state.

## Development

```bash
npm install
npm run dev          # Vite dev server
```

Unit tests are fully offline — fetch is mocked and no real EventSource or
backend is used:

```bash
npx vitest run       # one-shot CI mode
npx vitest           # watch mode
```

Production build (strict TS, zero errors required):

```bash
npm run build        # outputs to dist/
npm run preview      # serve the built bundle locally
```

## Environment variables

All variables are build-time (Vite `import.meta.env`). A `.env.local` file works.

| Variable         | Default | Meaning |
| ---------------- | ------- | ------- |
| `VITE_API_BASE`  | `/api`  | Base path of the backend REST + SSE API. Same-origin deployments (reverse proxy) need no change. |
| `VITE_API_TOKEN` | _unset_ | Optional bearer token. When set, every REST request and SSE stream open sends `Authorization: Bearer <token>`. Leave unset for unauthenticated local use. |

## Backend contract (implemented)

- `POST /api/runs` — start a run (`ticker`, `date`, `asset_type`, `provider`,
  optional `instructions`). Returns `202 {job_id, status}`; `422 {detail}` for
  static validation errors; `429` with `Retry-After` at capacity.
- `GET /api/runs/{id}` — status incl. `effective_provider` fallback visibility.
- `GET /api/runs/{id}/events` — SSE stream. `id:` carries the integer sequence,
  data is JSON `{seq, ts, event}` with `event.type` of `agent_status | report |
  decision`; a `DONE` sentinel terminates the stream. Reconnects replay from the
  last received `Last-Event-ID`.
- `GET /api/runs` — run list; `DELETE /api/runs/{id}` — cancel request.
- `GET /api/health` — liveness.

`instructions` are only valid when `provider` is `ta_plugins` (the form gates
the textarea accordingly); max 4000 characters, enforced live in the UI.

## Reverse proxy note (SSE)

The event stream is long-lived Server-Sent Events. **Disable response buffering
for the API path** or events arrive in delayed bursts:

```nginx
location /api/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Connection '';
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 3600s;
}
```

Equivalent Caddy handle block: `reverse_proxy` with `flush_interval -1`.

## Security notes

- LLM-generated report and decision text is untrusted input. It is rendered as
  plain text or through a sanitizing markdown renderer (`SafeMarkdown`); React
  default escaping everywhere; `dangerouslySetInnerHTML` is never used.
- The optional bearer token lives only in the client bundle env — use a backend
  reverse proxy with real auth for anything internet-facing.

## Layout

```text
src/
  api/        typed ApiClient + wire types (+ offline tests)
  state/      validation + SSE event-to-pipeline reducer (+ tests)
  components/ badges, pipeline stages, report sections, decision card, safe markdown
  views/      AnalyzeForm, RunView, RunsList (+ form tests)
  test/       vitest setup (jsdom + jest-dom matchers)
```
