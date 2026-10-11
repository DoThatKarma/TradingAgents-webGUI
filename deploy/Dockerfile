# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# TradingAgents-webGUI server image (ADR 0010).
#
# Build context is the REPOSITORY ROOT (compose sets "build: { context: .. }"):
#
#   docker build -f deploy/Dockerfile .
#
# Stage 1 builds the SPA (frontend/dist is git-ignored, so it is built in-image
# to keep the image self-contained and CI-reproducible). Stage 2 installs the
# server package from server/ (pyproject: ta-webgui-server, packages.find
# include=["app*"]) and serves the SPA from TA_WEBGUI_STATIC_DIR=/webui
# (single-process hosting, ADR 0006).
#
# NOTE: run exactly ONE container of this image. The job/pool state is
# in-process; multiple replicas would split job state (see ADR 0010).
#
# TIP: for production pin the base images to digests, e.g.
#   docker build --pull && docker image inspect --format '{{.RepoDigests}}'
# ---------------------------------------------------------------------------

# --- Stage 1: build the SPA -------------------------------------------------
FROM node:22-alpine AS webui-build

WORKDIR /frontend

# package files first for better layer caching
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/index.html frontend/vite.config.ts frontend/tsconfig.json \
     frontend/tsconfig.app.json frontend/tsconfig.node.json ./
COPY frontend/src ./src
RUN npm run build

# --- Stage 2: server ---------------------------------------------------------
FROM python:3.12-slim AS server

LABEL org.opencontainers.image.title="tradingagents-webgui-server" \
      org.opencontainers.image.description="TradingAgents-webGUI FastAPI server with bundled SPA" \
      org.opencontainers.image.source="https://github.com/DoThatKarma/TradingAgents-webGUI" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TA_WEBGUI_STATIC_DIR=/webui

# non-root runtime user (uid 10001, no shell)
RUN groupadd -g 10001 ta && useradd -u 10001 -g ta -m -s /usr/sbin/nologin ta

# server package (app.* becomes importable via pip install)
COPY server /srv/server
RUN pip install --no-cache-dir /srv/server

# bundled SPA from stage 1 (root-owned, world-readable is fine for static files)
COPY --from=webui-build /frontend/dist /webui

# health probe script (used by HEALTHCHECK and by external schedulers)
COPY deploy/healthcheck.py /usr/local/bin/healthcheck.py

# writable persistence dir for run reports (mounted as a volume in compose)
RUN mkdir -p /data && chown ta:ta /data

USER ta

WORKDIR /srv

EXPOSE 8000

# Bind 0.0.0.0 INSIDE the container: the container network is isolated and
# Traefik is the only ingress path. Host binding hygiene is enforced by the
# compose topology (no published ports) instead of the app bind address.
CMD ["uvicorn", "app.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

# GET /api/health is always open (auth-exempt) — safe for proxy health checks.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "/usr/local/bin/healthcheck.py"]
