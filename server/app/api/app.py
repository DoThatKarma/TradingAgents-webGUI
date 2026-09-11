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
"""

from __future__ import annotations

import hmac
import json
import os
from collections.abc import Iterable, Iterator
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sse_starlette.event import ServerSentEvent
from sse_starlette.sse import EventSourceResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.engine.runner import RunEngine
from app.jobs.manager import (
    JobManager,
    JobNotTerminalError,
    PoolFullError,
    SubscriberLimitError,
    validate_instructions,
)

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

    return app
