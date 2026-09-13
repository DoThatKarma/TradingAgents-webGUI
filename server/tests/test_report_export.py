"""Report export: GET /api/runs/{id}/report downloads a completed run's Markdown.

Covers the happy path (metadata header, report sections, final decision,
Content-Disposition attachment), 404 semantics for running/no-report/unknown
runs, auth enforcement when the bearer gate is configured, and filename
sanitization. All scenarios run offline against the injectable factory
(ADR 0003).
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from app.api.app import create_app
from app.engine.runner import RunEngine
from app.jobs.report import report_filename
from tests.stubs import StubGraphFactory, StubRunner

_TOKEN_ENV = "TA_WEBGUI_API_TOKEN"
_TOKEN = "test-bearer-token-report"
_RUN_BODY: dict[str, Any] = {"ticker": "NVDA", "date": "2025-01-10"}


class BlockingRunner(StubRunner):
    """StubRunner whose stream blocks until released (emulates a long run)."""

    def __init__(self) -> None:
        super().__init__(snapshots=[], signal="Buy")
        self.release = threading.Event()

    def stream(self) -> Iterator[dict[str, Any]]:
        while not self.release.is_set():
            time.sleep(0.01)
        return iter([])


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _app(runner: StubRunner | None = None) -> Any:
    return create_app(engine=RunEngine(graph_factory=StubGraphFactory(runner)))


async def _submit_and_wait(
    client: httpx.AsyncClient,
    body: dict[str, Any] | None = None,
    request_headers: dict[str, str] | None = None,
) -> str:
    created = await client.post("/api/runs", json=body or _RUN_BODY, headers=request_headers)
    assert created.status_code == 202, created.text
    job_id = created.json()["job_id"]
    for _ in range(500):
        status = (await client.get(f"/api/runs/{job_id}", headers=request_headers)).json()
        if status["status"] == "completed":
            return job_id
        await asyncio.sleep(0.01)
    raise AssertionError("job did not complete in time")


def test_report_download_completed_run() -> None:
    async def scenario() -> None:
        async with _client(_app()) as client:
            job_id = await _submit_and_wait(client)
            response = await client.get(f"/api/runs/{job_id}/report")
            assert response.status_code == 200
            assert response.headers["content-type"] == "text/markdown; charset=utf-8"
            disposition = response.headers["content-disposition"]
            assert disposition.startswith("attachment;")
            assert 'filename="tradingagents-NVDA-2025-01-10.md"' in disposition
            body = response.text
            # Metadata header.
            assert "# TradingAgents Analysis Report" in body
            assert "**Ticker:** NVDA" in body
            assert "**Analysis date:** 2025-01-10" in body
            assert "**Asset type:** stock" in body
            assert "**Provider:** direct" in body
            assert "**Custom instructions:** no" in body
            assert "**Completed at:**" in body
            # At least one stored report section with its content.
            assert "## Market Analyst Report" in body
            assert "Market: NVDA technicals strong" in body
            assert "## Sentiment Analyst Report" in body
            # Final decision line from the stored decision event.
            assert "## Final Decision" in body
            assert "**Signal:** Buy" in body
            assert "final decision: buy" in body

    asyncio.run(scenario())


def test_report_metadata_reflects_instructions() -> None:
    body_config = {
        "ticker": "BTC-USD",
        "date": "2025-02-01",
        "provider": "ta_plugins",
        "instructions": "Focus on downside risk.",
    }

    async def scenario() -> None:
        async with _client(_app()) as client:
            job_id = await _submit_and_wait(client, body_config)
            response = await client.get(f"/api/runs/{job_id}/report")
            assert response.status_code == 200
            assert "**Provider:** ta_plugins" in response.text
            assert "**Custom instructions:** yes" in response.text
            assert (
                'filename="tradingagents-BTC-USD-2025-02-01.md"'
                in (response.headers["content-disposition"])
            )

    asyncio.run(scenario())


def test_report_running_run_404() -> None:
    runner = BlockingRunner()

    async def scenario() -> None:
        async with _client(_app(runner)) as client:
            created = await client.post("/api/runs", json=_RUN_BODY)
            job_id = created.json()["job_id"]
            for _ in range(500):
                status = (await client.get(f"/api/runs/{job_id}")).json()
                if status["status"] == "running":
                    break
                await asyncio.sleep(0.01)
            assert status["status"] == "running"
            response = await client.get(f"/api/runs/{job_id}/report")
            assert response.status_code == 404
            assert response.json() == {"detail": "report not available"}
            # Completed without any stored report content stays a 404.
            runner.release.set()
            for _ in range(500):
                status = (await client.get(f"/api/runs/{job_id}")).json()
                if status["status"] == "completed":
                    break
                await asyncio.sleep(0.01)
            assert status["status"] == "completed"
            assert (await client.get(f"/api/runs/{job_id}/report")).status_code == 404

    asyncio.run(scenario())


def test_report_unknown_run_404() -> None:
    async def scenario() -> None:
        async with _client(_app()) as client:
            response = await client.get("/api/runs/unknown-id/report")
            assert response.status_code == 404
            assert response.json() == {"detail": "unknown run"}

    asyncio.run(scenario())


def test_report_requires_token_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_TOKEN_ENV, _TOKEN)
    headers = {"Authorization": f"Bearer {_TOKEN}"}

    async def scenario() -> None:
        async with _client(_app()) as client:
            denied = await client.get("/api/runs/unknown-id/report")
            assert denied.status_code == 401
            assert denied.json() == {"detail": "unauthorized"}
            job_id = await _submit_and_wait(
                client, dict(_RUN_BODY), {"Authorization": f"Bearer {_TOKEN}"}
            )
            ok = await client.get(f"/api/runs/{job_id}/report", headers=headers)
            assert ok.status_code == 200
            assert ok.headers["content-type"] == "text/markdown; charset=utf-8"

    asyncio.run(scenario())


def test_report_filename_sanitization() -> None:
    assert report_filename("NVDA", "2025-01-10") == "tradingagents-NVDA-2025-01-10.md"
    assert report_filename("BRK.A", "2025-01-10") == "tradingagents-BRK.A-2025-01-10.md"
    assert report_filename("BAD TICKER!", "2025-01-10") == ("tradingagents-BADTICKER-2025-01-10.md")
    assert report_filename("../../etc/passwd", "2025-01-10") == (
        "tradingagents-....etcpasswd-2025-01-10.md"
    )
    # Nothing survives sanitization: stable fallback instead of an empty name.
    assert report_filename("!!!", "2025-01-10") == "tradingagents-run-2025-01-10.md"
