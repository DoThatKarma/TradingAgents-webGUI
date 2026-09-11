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

The engine is injectable so tests run fully offline (ADR 0003). The API
assumes a trusted/localhost deployment until authentication lands.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sse_starlette.event import ServerSentEvent
from sse_starlette.sse import EventSourceResponse

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
    app = FastAPI(title="TradingAgents-webGUI", version="0.1.0")
    if manager is None:
        manager = JobManager(engine or RunEngine())

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
        cursor: int = Query(default=0, ge=0),
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
