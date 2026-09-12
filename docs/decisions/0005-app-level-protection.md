# ADR 0005: App-level protection (optional bearer token + security headers)

Status: accepted

Context: the GUI backend will be exposed on a public website behind a reverse
proxy with TLS termination at the proxy. The project deliberately avoids heavy
auth machinery inside the app; protection is layered instead.

Decision:

- Reverse proxy first: TLS termination, rate limiting and public exposure
  belong at the proxy (nginx/Caddy). uvicorn binds to 127.0.0.1 by default and
  never faces the internet directly.
- Optional app token as complement: when TA_WEBGUI_API_TOKEN is set, every
  /api/* route except GET /api/health (kept open for proxy health checks)
  requires Authorization: Bearer <token>. Comparison is constant-time
  (hmac.compare_digest). 401 responses carry a static body and
  WWW-Authenticate: Bearer - no reflected input, no token echo; the token
  never appears in logs or response bodies. When unset, behavior is identical
  to local development.
- Security headers on every response: X-Content-Type-Options nosniff,
  X-Frame-Options DENY, Referrer-Policy no-referrer, Cache-Control no-store
  (pure-ASGI middleware, outermost in the stack, so 401s and SSE streams
  carry them too).
- CORS disabled by default (same-origin behind the proxy). If CORS is ever
  configured it must be default-deny with an explicit allowlist - never a
  wildcard origin together with credentials.

Alternatives considered: full session/JWT auth (rejected: out of scope; proxy
plus static token covers the requested modest protection); per-route
authentication dependencies (rejected: a middleware gate covers all routes
uniformly and stays out of handler signatures).

Consequences: local development is unchanged without the variable; public
deployments follow the README Running & exposing sketches (nginx/Caddy,
proxy_buffering off for SSE). The frontend must send the bearer header on
every API call when a deployment sets a token; the static 401 body is
{"detail": "unauthorized"}.
