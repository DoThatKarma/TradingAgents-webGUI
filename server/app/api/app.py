"""FastAPI application: REST control plane + SSE run streams.

Endpoints (all JSON):
- ``POST /api/runs`` — start a run, returns 202 with a job id
- ``GET /api/runs`` — list all non-evicted runs
- ``GET /api/runs/{id}`` — job status snapshot (includes ``effective_provider``)
- ``DELETE /api/runs/{id}`` — evict a terminal run; a non-terminal run is
  cancelled instead
- ``GET /api/runs/{id}/events`` — SSE stream of run events; supports replay
  from a cursor via the ``cursor`` query parameter or the standard
  ``Last-Event-ID`` header (browser EventSource reconnects send it
  automatically; a valid header takes precedence over ``cursor``). Each SSE
  event's ``id`` is the event log ``seq``. Keep-alive pings are enabled.
- ``GET /api/runs/{id}/report`` — download a completed run's full report as
  a ``text/markdown`` attachment (metadata header, one section per stored
  report, final decision); runs that are not completed or without stored
  report content return 404
- ``GET /api/health`` — liveness probe

Error hygiene (hardening): client-facing 422/429 details are static text —
validation problems are never echoed back, and job failures surface only a
short category from the manager. Capacity errors return ``429`` with a
``Retry-After`` header (pool full, subscriber cap).

The engine is injectable so tests run fully offline (ADR 0003).

App-level protection (ADR 0005): an optional static bearer-token gate for
``/api/*`` (``TA_WEBGUI_API_TOKEN``; ``GET /api/health`` stays open for proxy
health checks) plus minimal security headers on every response. Both are
pure-ASGI middlewares; CORS is disabled by default (same-origin behind a
reverse proxy). Bind uvicorn to localhost unless the token is set.

Single-process SPA hosting (ADR 0006): when ``TA_WEBGUI_STATIC_DIR`` points to
a built frontend dist (or a bundled ``webui/`` is auto-detected next to the
server package), the app serves ``/`` and ``/assets/*`` plus an SPA fallback
for unknown non-API GET paths; unknown ``/api/*`` paths keep their 404.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field, field_validator
from sse_starlette.event import ServerSentEvent
from sse_starlette.sse import EventSourceResponse
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.engine.runner import RunEngine
from app.jobs.manager import (
    JobManager,
    JobNotTerminalError,
    PoolFullError,
    ReportNotReadyError,
    SubscriberLimitError,
    validate_instructions,
)
from app.jobs.report import build_report_markdown, report_filename

_RETRY_AFTER_SECONDS = "5"

_STATIC_INVALID_REQUEST = "invalid run request"

_STATIC_UNAUTHORIZED = "unauthorized"

_TOKEN_ENV_VAR = "TA_WEBGUI_API_TOKEN"

# Paths inside /api/* that stay reachable without a token (proxy health checks).
_AUTH_EXEMPT_PATHS = frozenset({"/api/health"})

_SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("Cache-Control", "no-store"),
)

# Lowercase raw byte names: replace (not duplicate) same-named inner headers.
_SECURITY_HEADER_NAMES = frozenset(name.encode("latin-1").lower() for name, _ in _SECURITY_HEADERS)

_LOGGER = logging.getLogger(__name__)

_STATIC_DIR_ENV_VAR = "TA_WEBGUI_STATIC_DIR"

# Directory name auto-detected next to the server package (release zip layout
# and dev-tree convenience).
_BUNDLED_STATIC_DIRNAME = "webui"


def _bundled_static_candidates() -> list[Path]:
    """Auto-detect candidates for a bundled SPA next to the server package.

    Priority order (release layout first):
    1. ``webui/`` sibling of the ``app`` package (release zip layout:
       ``server/`` and ``webui/`` side by side)
    2. ``webui/`` next to the ``server/`` directory (dev-tree convenience)

    Sealed at module level so tests can patch it without touching disk.
    """
    server_dir = Path(__file__).resolve().parent.parent.parent  # .../server
    return [
        server_dir / _BUNDLED_STATIC_DIRNAME,
        server_dir.parent / _BUNDLED_STATIC_DIRNAME,
    ]


def _looks_like_spa_dist(path: Path) -> bool:
    """A usable SPA dist must be a directory containing ``index.html``."""
    return path.is_dir() and (path / "index.html").is_file()


def _resolve_static_dir() -> Path | None:
    """Resolve the SPA dist directory: explicit env var wins over auto-detect.

    Returns ``None`` when hosting stays disabled. Fail-open by design: a
    missing or invalid target only logs a warning and leaves the app in
    API-only mode — a misconfigured path must never crash the factory.
    """
    configured = os.environ.get(_STATIC_DIR_ENV_VAR, "").strip()
    if configured:
        # Whitespace-only values are treated as unset (same policy as the
        # bearer token): no gate/hosting that only matches after stripping.
        candidate = Path(configured).expanduser()
        if _looks_like_spa_dist(candidate):
            return candidate
        if candidate.is_dir():
            _LOGGER.warning(
                "%s points to %s which lacks index.html; "
                "single-process SPA hosting stays disabled (API-only)",
                _STATIC_DIR_ENV_VAR,
                candidate,
            )
        else:
            _LOGGER.warning(
                "%s points to missing directory %s; "
                "single-process SPA hosting stays disabled (API-only)",
                _STATIC_DIR_ENV_VAR,
                candidate,
            )
        return None
    for candidate in _bundled_static_candidates():
        if _looks_like_spa_dist(candidate):
            return candidate
        if candidate.is_dir():
            # Present but not a built SPA: suspicious, worth a warning;
            # fully absent candidates are the normal dev-tree case (silent).
            _LOGGER.warning(
                "Auto-detected SPA directory %s lacks index.html; "
                "single-process SPA hosting stays disabled (API-only)",
                candidate,
            )
    return None


def _mount_static_spa(app: FastAPI, static_dir: Path) -> None:
    """Serve the built SPA from the API process (opt-in; ADR 0006).

    ``/assets/*`` maps onto the dist's ``assets/`` directory, ``/`` serves
    ``index.html``, and unknown non-``/api`` GET paths fall back to
    ``index.html`` so client-side routing works. Registered after all API
    routes so exact API routes keep priority; unknown ``/api/*`` paths keep
    their normal 404 (the catch-all explicitly refuses them).

    Static files stay outside the bearer gate (it covers ``/api/*`` only) —
    correct for the local single-user release; public deployments should
    still front the app with a reverse proxy (ADR 0005/0006).
    """
    index_file = static_dir / "index.html"
    assets_dir = static_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    resolved_root = static_dir.resolve()

    def _static_file_or_index(spa_path: str) -> FileResponse:
        # Serve real files from the dist when they exist (e.g. favicon),
        # fall back to index.html for client-side routes.
        try:
            candidate = (static_dir / spa_path).resolve()
            candidate.relative_to(resolved_root)
            is_file = bool(spa_path) and candidate.is_file()
        except (ValueError, OSError) as exc:
            # Malformed input (e.g. a null byte) or a path traversal attempt:
            # never serve anything outside the dist, never raise a 500.
            raise HTTPException(status_code=404, detail="Not Found") from exc
        if is_file:
            return FileResponse(candidate)
        return FileResponse(index_file)

    @app.get("/", include_in_schema=False)
    def spa_index() -> FileResponse:
        return FileResponse(index_file)

    @app.get("/{spa_path:path}", include_in_schema=False)
    def spa_fallback(spa_path: str) -> FileResponse:
        # Unknown /api/* paths keep the framework's normal 404 — the SPA
        # fallback must never shadow API error semantics.
        if spa_path == "api" or spa_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        return _static_file_or_index(spa_path)


class BearerTokenMiddleware:
    """Optional static bearer-token gate for ``/api/*`` (ADR 0005).

    Enabled only when a token is configured (``TA_WEBGUI_API_TOKEN``); when
    unset the middleware is a pass-through, so local development behaves
    exactly as before. The token is a deployment secret: it is compared in
    constant time and never logged or reflected — 401 responses carry a
    static body only.
    """

    def __init__(self, app: ASGIApp, token: str | None) -> None:
        self.app = app
        # Whitespace-only values are treated as unset to avoid a gate that
        # only matches after accidental stripping on both sides.
        self._enabled = bool(token and token.strip())
        self._expected = token.strip().encode("utf-8") if self._enabled else b""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._enabled or scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        protected_path = path == "/api" or path.startswith("/api/")
        if not protected_path or path in _AUTH_EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return
        presented = _bearer_token_from_headers(scope.get("headers", ()))
        if presented is None or not hmac.compare_digest(presented, self._expected):
            # Static body only: never reflect the presented credential and
            # never echo the configured token back to the client.
            response = JSONResponse(status_code=401, content={"detail": _STATIC_UNAUTHORIZED})
            response.headers["WWW-Authenticate"] = "Bearer"
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _bearer_token_from_headers(raw_headers: Iterable[tuple[bytes, bytes]]) -> bytes | None:
    """Extract the credential from ``Authorization: Bearer <token>`` (bytes safe).

    Returns ``None`` when the header is absent or does not use the Bearer
    scheme. Returns raw bytes so the comparison stays constant-time even for
    non-ASCII input; the header value is never logged or reflected.
    """
    for name, value in raw_headers:
        if name == b"authorization":
            parts = value.split(None, 1)
            # RFC 9110: auth schemes are case-insensitive.
            if len(parts) == 2 and parts[0].lower() == b"bearer":
                return parts[1].strip()
            return None
    return None


class SecurityHeadersMiddleware:
    """Add minimal security headers to every response (ADR 0005).

    ``X-Content-Type-Options`` defeats MIME sniffing, ``X-Frame-Options``
    blocks framing, ``Referrer-Policy`` avoids leaking URLs via Referer, and
    ``Cache-Control: no-store`` keeps run data out of shared caches.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                # Replace same-named headers from inner handlers (e.g. the
                # SSE response's own Cache-Control) so each protected header
                # is emitted exactly once with the canonical value.
                headers = [
                    (key, value)
                    for key, value in message.setdefault("headers", [])
                    if key not in _SECURITY_HEADER_NAMES
                ]
                headers.extend(
                    (name.encode("latin-1"), value.encode("latin-1"))
                    for name, value in _SECURITY_HEADERS
                )
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


class RunRequest(BaseModel):
    """Body of POST /api/runs (hard caps + safe charset validation)."""

    ticker: str = Field(pattern=r"^[A-Za-z0-9.\-:^]{1,16}$")
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    asset_type: Literal["stock", "crypto", "polymarket"] = "stock"
    provider: Literal["direct", "ta_plugins"] = "direct"
    instructions: str | None = Field(default=None, max_length=4000)

    @field_validator("instructions")
    @classmethod
    def _reject_control_chars(cls, value: str | None) -> str | None:
        # Shared rule with the manager boundary (M1: static 422, no reflection).
        validate_instructions(value)
        return value


class RunCreated(BaseModel):
    """Response of POST /api/runs."""

    job_id: str
    status: str = "queued"


class JobStatusResponse(BaseModel):
    """Response of GET /api/runs/{id} and elements of GET /api/runs."""

    job_id: str
    ticker: str
    date: str
    asset_type: str
    provider: str
    effective_provider: str | None = None
    has_instructions: bool
    status: str
    created_at: float
    error: str | None = None
    event_count: int


def _parse_last_event_id(raw: str | None) -> int | None:
    """Parse a Last-Event-ID header; ``None`` when absent.

    Malformed values (non-digits, empty, absurdly long digit strings) raise
    400 — silently falling back to the cursor could replay events the client
    already processed.
    """
    if raw is None:
        return None
    value = raw.strip()
    # isascii first: isdigit() accepts e.g. '²', which int() rejects.
    if not value.isascii() or not value.isdigit() or len(value) > 18:
        raise HTTPException(status_code=400, detail="invalid Last-Event-ID header")
    return int(value)


def create_app(engine: RunEngine | None = None, manager: JobManager | None = None) -> FastAPI:
    """Build the FastAPI app around a (shared) JobManager.

    ``manager`` is injectable for tests that need non-default pool limits
    (ADR 0003); when omitted, a manager is built around ``engine``.
    """
    # /docs, /redoc and /openapi.json expose the full API schema; they are
    # enabled only when the bearer gate is off (local development).
    token = os.environ.get(_TOKEN_ENV_VAR)
    docs_enabled = not (token and token.strip())
    app = FastAPI(
        title="TradingAgents-webGUI",
        version="0.1.0",
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    if manager is None:
        manager = JobManager(engine or RunEngine())

    # App-level protection (ADR 0005). The optional bearer gate reads the
    # token at factory time, so apps built without TA_WEBGUI_API_TOKEN behave
    # exactly as before. SecurityHeadersMiddleware is added last, making it
    # outermost in the Starlette stack: every response, including 401s from
    # the bearer gate, carries the headers. CORS is intentionally not
    # configured - same-origin behind a reverse proxy, deny by default.
    app.add_middleware(BearerTokenMiddleware, token=token)
    app.add_middleware(SecurityHeadersMiddleware)

    @app.exception_handler(RequestValidationError)
    async def static_validation_errors(_request: Any, _exc: RequestValidationError) -> JSONResponse:
        # Never reflect request payloads back to clients (M1).
        return JSONResponse(status_code=422, content={"detail": _STATIC_INVALID_REQUEST})

    @app.post("/api/runs", status_code=202, response_model=RunCreated)
    def start_run(request: RunRequest) -> RunCreated:
        try:
            job_id = manager.submit_run(request.model_dump())
        except PoolFullError as exc:
            raise HTTPException(
                status_code=429,
                detail="run capacity reached; retry later",
                headers={"Retry-After": _RETRY_AFTER_SECONDS},
            ) from exc
        except ValueError as exc:
            # Static detail only: no echo of the rejected input (M1).
            raise HTTPException(status_code=422, detail=_STATIC_INVALID_REQUEST) from exc
        return RunCreated(job_id=job_id)

    @app.get("/api/runs", response_model=list[JobStatusResponse])
    def list_runs() -> list[JobStatusResponse]:
        return [JobStatusResponse(**snapshot) for snapshot in manager.list_jobs()]

    @app.get("/api/runs/{job_id}", response_model=JobStatusResponse)
    def get_status(job_id: str) -> JobStatusResponse:
        try:
            snapshot = manager.status(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown run") from exc
        return JobStatusResponse(**snapshot)

    @app.get(
        "/api/runs/{job_id}/report",
        responses={
            404: {"description": "Run unknown or no completed report available"},
        },
    )
    def download_report(job_id: str) -> Response:
        """Download a completed run's full report as Markdown (attachment)."""
        try:
            material = manager.export_report_material(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown run") from exc
        except ReportNotReadyError as exc:
            # Static detail: no server-side error details reflected (ADR 0005).
            raise HTTPException(status_code=404, detail="report not available") from exc
        markdown = build_report_markdown(material)
        filename = report_filename(material["ticker"], material["date"])
        return Response(
            content=markdown,
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )

    @app.delete("/api/runs/{job_id}")
    def delete_run(job_id: str) -> dict[str, Any]:
        try:
            manager.delete_job(job_id)  # evicts when terminal
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown run") from exc
        except JobNotTerminalError:
            cancelled = manager.cancel(job_id)
            return {"job_id": job_id, "deleted": False, "cancelled": cancelled}
        return {"job_id": job_id, "deleted": True, "cancelled": False}

    @app.get("/api/runs/{job_id}/events")
    def stream_events(
        job_id: str,
        cursor: int = Query(default=0, ge=0, le=10**18),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> EventSourceResponse:
        header_cursor = _parse_last_event_id(last_event_id)
        start = header_cursor if header_cursor is not None else cursor
        try:
            envelopes = manager.subscribe(job_id, cursor=start)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown run") from exc
        except SubscriberLimitError as exc:
            raise HTTPException(
                status_code=429,
                detail="subscriber limit reached for this run",
                headers={"Retry-After": _RETRY_AFTER_SECONDS},
            ) from exc

        def sse() -> Iterator[ServerSentEvent]:
            for envelope in envelopes:
                yield ServerSentEvent(
                    id=str(envelope["seq"]),  # sse-starlette requires str ids
                    event=envelope["event"]["type"],
                    data=json.dumps(envelope, default=str),
                )

        # ping keepalive + periodic queue wakeups in the manager keep the
        # stream responsive to client disconnects (generator cleanup runs in
        # its finally block and unregisters the subscriber).
        return EventSourceResponse(sse(), ping=15.0)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # Opt-in single-process SPA hosting (ADR 0006). Registered after every API
    # route so exact API routes keep priority and the catch-all fallback only
    # ever serves what nothing else matched. Static hosting is disabled unless
    # a valid SPA dist is found; failures log a warning and fail open to the
    # previous API-only behaviour. The security-headers middleware (outermost)
    # also wraps static responses; the bearer gate still covers /api/* only.
    static_dir = _resolve_static_dir()
    if static_dir is not None:
        _LOGGER.info("Serving SPA from %s", static_dir)
        _mount_static_spa(app, static_dir)

    return app
