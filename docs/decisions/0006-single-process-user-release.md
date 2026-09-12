# ADR 0006: Single-process user release (opt-in SPA hosting)

Status: accepted

Context: the target user for the downloadable release zip is a local,
single-user operator who wants "unzip, install, run, browse to
http://127.0.0.1:8000". Requiring Node.js, a frontend build step, or a second
web server for static files would break that promise. The public-website
deployment story (reverse proxy owns TLS, rate limiting, exposure; ADR 0005)
remains unchanged.

Decision:

- Opt-in single-process SPA hosting in the FastAPI app. When
  ``TA_WEBGUI_STATIC_DIR`` points to a built frontend dist directory — or a
  bundled ``webui/`` directory is auto-detected next to the server package —
  the backend process serves the built SPA in addition to the API:
  ``/assets/*`` maps to the dist's ``assets/`` directory, ``/`` serves
  ``index.html``, and unknown non-``/api`` GET paths fall back to
  ``index.html`` so client-side routing works.
- Explicit configuration wins over auto-detection. A configured directory
  that is missing or lacks ``index.html`` disables hosting with a log warning
  and fails open to the previous API-only behaviour — a bad path never
  crashes the factory.
- API semantics are untouched: exact API routes keep priority over the
  fallback (it is registered last), and unknown ``/api/*`` paths keep their
  normal 404 instead of returning HTML.
- The security-headers middleware (outermost in the ASGI stack, ADR 0005)
  wraps static responses too; static files are not covered by the bearer
  gate, which deliberately covers ``/api/*`` only. This is correct for the
  local single-user release; run scripts bind to ``127.0.0.1`` by default.
- Public deployments should still front the app with a reverse proxy per
  ADR 0005 (TLS termination, rate limiting). Note the frontend reads
  ``VITE_API_TOKEN`` at build time, so a token-protected public deployment
  needs a frontend rebuilt with that value baked in — packaged builds have no
  token built in.

Alternatives considered: a separate static web server/container (rejected:
second process contradicts the one-command user experience); serving static
files from an outer reverse proxy even locally (rejected: extra local
software contradicts the one-command goal); always-on hosting with a built-in
dist (rejected: dev tree and API-only deployments must keep current
behaviour).

Consequences: the release zip ships the built frontend as ``webui/`` next to
``server/`` and auto-detection finds it; developers keep API-only behaviour
unless they opt in explicitly. One process serves everything, matching the
run-script contract. Public/internet-facing deployments remain a
proxy-fronted scenario (ADR 0005) with the token caveat above.
