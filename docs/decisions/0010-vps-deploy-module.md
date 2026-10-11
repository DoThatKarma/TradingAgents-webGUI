# ADR 0010: VPS deploy module — Cloudflare Tunnel + Traefik edge, GHCR image

Status: accepted

## Context

The WebGUI backend (ADR 0006: single-process FastAPI that also hosts the built
SPA) needs a small, reproducible self-hosting story for a personal or team VPS.
Constraints and facts that shaped the decision:

- **No inbound ports.** A home-lab or cheap VPS should not expose
  `:80/:443` and become every scanner's petri dish. The owner already uses
  Cloudflare for DNS.
- **The app protects itself** (ADR 0005): optional constant-time bearer gate
  `TA_WEBGUI_API_TOKEN` on `/api/*` with `GET /api/health` always open for
  proxy health checks, plus minimal security headers on every response.
- **SSE is load-bearing**: `GET /api/runs/{id}/events` is a long-lived
  Server-Sent-Events stream with keepalives; any buffering/compression in the
  proxy path would break live runs. Cloudflare's free-tier ~100 s idle timeout
  is handled by the backend keepalives, but nothing in the proxy may add
  buffering or `text/event-stream` transforms.
- **Single process, single replica** (ADR 0006): job pool and SSE subscriber
  state live in-process; the deployment must run exactly one server container
  and say so loudly.
- **CI already exists** (ADR 0009): tag `v*` → release zip; Pages is
  dispatch-only. An image build slot was missing.
- Phase 2 of ADR 0009 puts Pages/CORS-relevant backend URLs into play — the
  Pages site and the API behind this deployment must share one canonical URL.

## Decision

**Topology: cloudflared → Traefik v3 → uvicorn, all on one compose network,
zero published ports.**

```
Cloudflare edge ==outbound tunnel==> cloudflared --> traefik:80 --> server:8000
```

- **cloudflared (token mode)**: `cloudflare/cloudflared` running
  `tunnel --no-autoupdate run --token ...`. Token mode is recommended over
  credentials-file mode: public hostnames and ingress are configured in the
  Zero Trust dashboard, nothing routing-related lives on the VPS, and rotation
  is a dashboard action. A file-mode alternative (`deploy/cloudflared/` +
  `deploy/compose.override.filemode.yml`) exists for GitOps-style setups.
- **Traefik v3.3 as internal edge router**: one plaintext entrypoint `web` on
  `:80`, never published to the host. cloudflared points at the single static
  URL `http://traefik:80`; **routing lives in docker-provider labels on the
  server service** (Host/PathPrefix rules + middleware chains), not in
  cloudflared ingress rules. One routing truth, next to the app it routes to,
  testable with `docker compose config` and changeable with
  `docker compose up -d`.
  - `forwardedheaders.insecure=true` on `web` is safe **only because** the
    entrypoint is unreachable from outside the compose network: the sole
    client is cloudflared. It lets the rate limiter key on the real client IP
    relayed from Cloudflare via `X-Forwarded-For` instead of lumping all
    clients into one tunnel-bucket.
- **Middlewares (labels, v3 syntax)**:
  - `ratelimit` on `/api` (default average 10, burst 20 — env-tunable).
  - `inflightreq` `amount` (the current v3 field; `amountLimit` is deprecated)
    as a global concurrency cap that also bounds simultaneous SSE streams.
  - `headers`: HSTS (only meaningful at an edge that serves HTTPS to real
    browsers — TLS terminates at Cloudflare) plus nosniff/frame-deny/
    referrer as defense-in-depth under the app's own headers.
  - `compress` **on the SPA router only**, with `text/event-stream` explicitly
    excluded as belt-and-braces. The `/api` router deliberately carries no
    compress middleware so SSE can never be transformed or buffered.
- **Auth layering**: the app's bearer gate stays authoritative for `/api/*`
  (do not duplicate auth, complement it). An optional, commented-out Traefik
  `basicAuth` middleware is documented for the SPA router only — useful as a
  perimeter wall for shared/demo instances; it is kept off `/api` so proxy
  health checks and the bearer flow remain intact.
- **Server image** (`deploy/Dockerfile`): multi-stage — Node 20 builds the SPA,
  `python:3.12-slim` installs `server/` (pyproject `ta-webgui-server`, package
  `app`), serves the dist from `TA_WEBGUI_STATIC_DIR=/webui`, runs as a
  non-root user, and HEALTHCHECKs the auth-exempt `/api/health` with a
  stdlib-only probe (`deploy/healthcheck.py`). A BuildKit
  `deploy/Dockerfile.dockerignore` whitelists the build context so secrets
  (`deploy/.env`) and junk never enter it.
- **GHCR image** (`.github/workflows/docker.yml`): tag `v*` → build & push
  `ghcr.io/<lowercased-owner>/tradingagents-webgui-server` (`semver`, moving
  minor, and `latest` only on v-tags; `workflow_dispatch` produces a traceable
  `sha-*` tag and never moves `latest`). `linux/amd64` only — the target is a
  conventional Ubuntu VPS. GitHub Actions layer cache; minimal
  `permissions` (`packages: write`). The compose `image:` equals the GHCR tag,
  so `docker compose pull server && up -d` is the whole upgrade flow.
- **Operator guide**: `docs/deploy-vps.md` — Ubuntu LTS steps, Cloudflare
  dashboard checklist, verification incl. an SSE check, token rotation,
  upgrade flow, ASCII diagram, troubleshooting, and the "why no open ports"
  rationale.

## Consequences

- **VPS hardening shrinks to: keep the tunnel token secret.** No listener
  exists on the public interface; Cloudflare absorbs DDoS and hides the VPS IP.
- **Per-IP rate limiting depends on the X-Forwarded-For chain** from
  Cloudflare. If that chain is ever absent, the limiter degrades to one shared
  bucket — still a useful brake, documented in `.env.example` and the guide.
- **Scaling out is deliberately impossible without redesign**: replicas would
  split in-process job state. Scale vertically; revisit with a queue and
  external state only if multi-user demand becomes real.
- **`latest` is the upgrade channel**; pinning to `1.2.3` in `.env`-style
  override is available for change-averse operators. Manual dispatch builds
  can never contaminate `latest`.
- **TLS terminates at Cloudflare**; the tunnel link and the internal hop are
  encryption-protected (tunnel is mTLS-wrapped), but the traefik→server hop is
  plaintext HTTP inside the compose network. This is the accepted trade-off
  for a single-host stack; a `websecure` entrypoint with certificates would
  add complexity without a real threat here.
- **The GHCR package must be set public once** after the first tag push (or
  the VPS needs a `read:packages` PAT) — documented in
  `deploy/ghcr-workflow-design.md`.
- **Unverified-by-runtime caveat**: this module was authored without a Docker
  daemon available in the authoring environment (`docker --version` →
  no-docker). YAML was parsed and cross-references were checked manually;
  first real verification happens on the VPS / in CI build. The guide's Step 5
  is the acceptance test.

## Phasing (link to ADR 0009)

- **ADR 0009 phase 2** stands: Pages goes live and any CORS needs are solved
  against the **canonical backend URL** that this module establishes
  (`https://<DOMAIN>` — same-origin SPA hosting means CORS stays disabled by
  default; if Pages-served SPA + API split ever happens, CORS gets a decision
  of its own).
- **This ADR** supplies the backend URL, the image, and the ops flow that
  phase 2 documentation will reference.
- **Possible follow-ups (not decided here)**: digest-pinned base images,
  Cloudflare Access in front of the hostname for team-internal use, automated
  image rebuilds on base-image updates.

## Files

| File | Role |
| --- | --- |
| `deploy/docker-compose.yml` | Three-service stack, no published ports, Traefik labels |
| `deploy/Dockerfile` | Multi-stage server+SPA image |
| `deploy/Dockerfile.dockerignore` | Build-context whitelist |
| `deploy/healthcheck.py` | Stdlib `/api/health` probe for HEALTHCHECK |
| `deploy/.env.example` | All env vars documented |
| `deploy/cloudflared/config.yml` | File-mode ingress alternative |
| `deploy/cloudflared/README.md` | Token vs file mode, credentials hygiene |
| `deploy/compose.override.filemode.yml` | Compose override for file mode |
| `deploy/ghcr-workflow-design.md` | Design notes for `docker.yml` |
| `.github/workflows/docker.yml` | Tag-driven GHCR build/push |
| `docs/deploy-vps.md` | Operator guide |
