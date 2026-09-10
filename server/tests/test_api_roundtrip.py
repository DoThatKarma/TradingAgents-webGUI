"""HTTP roundtrip: start run, SSE stream with replay, status, cancel (ADR 0003)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from app.api.app import create_app
from app.engine.runner import RunEngine
from tests.stubs import StubGraphFactory


def parse_sse(text: str) -> list[dict[str, Any]]:
    """Parse a text/event-stream body into dicts with id/event/data keys."""
    events: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in text.splitlines():
        if line.startswith("data:"):
            current["data"] = json.loads(line[len("data:") :].strip())
        elif line.startswith("id:"):
            current["id"] = int(line[len("id:") :].strip())
        elif line.startswith("event:"):
            current["event"] = line[len("event:") :].strip()
        elif not line.strip() and current:
            events.append(current)
            current = {}
    if current:
        events.append(current)
    return events


def test_api_roundtrip() -> None:
    app = create_app(engine=RunEngine(graph_factory=StubGraphFactory()))

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/runs", json={"ticker": "NVDA", "date": "2025-01-10"})
            assert created.status_code == 202
            assert created.json()["status"] == "queued"
            job_id = created.json()["job_id"]

            status: dict[str, Any] = {}
            for _ in range(500):
                status = (await client.get(f"/api/runs/{job_id}")).json()
                if status["status"] == "completed":
                    break
                await asyncio.sleep(0.01)
            assert status["status"] == "completed"
            assert status["ticker"] == "NVDA"

            async with client.stream("GET", f"/api/runs/{job_id}/events") as stream:
                assert stream.status_code == 200
                body = (await stream.aread()).decode()
            events = parse_sse(body)
            assert events, "no SSE events received"
            assert [event["id"] for event in events] == list(range(1, len(events) + 1))
            assert events[0]["event"] == "agent_status"
            assert events[-1]["event"] == "decision"
            assert events[-1]["data"]["event"]["signal"] == "Buy"

            async with client.stream(
                "GET", f"/api/runs/{job_id}/events", headers={"Last-Event-ID": "2"}
            ) as stream:
                replay_body = (await stream.aread()).decode()
            replay = parse_sse(replay_body)
            assert replay[0]["data"]["seq"] == 3

            assert (await client.get("/api/health")).json() == {"status": "ok"}
            # DELETE on a terminal job evicts it (new hardening contract).
            deleted = await client.delete(f"/api/runs/{job_id}")
            assert deleted.status_code == 200
            assert deleted.json() == {"job_id": job_id, "deleted": True, "cancelled": False}
            # After eviction the job id is gone.
            assert (await client.get(f"/api/runs/{job_id}")).status_code == 404
            assert (await client.get("/api/runs/unknown-id")).status_code == 404

    asyncio.run(scenario())


def test_api_rejects_incomplete_run_config() -> None:
    app = create_app(engine=RunEngine(graph_factory=StubGraphFactory()))

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/runs", json={"ticker": "NVDA"})
            assert response.status_code == 422

    asyncio.run(scenario())
