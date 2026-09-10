"""Hardening behavior: pool limits, caps, TTL, lock timeout, validation (H1-H3, M1-M4)."""

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
from app.jobs.manager import (
    JobManager,
    JobNotTerminalError,
    PoolFullError,
    SubscriberLimitError,
)
from tests.stubs import StubGraphFactory, StubRunner, standard_snapshots
from tests.test_api_roundtrip import parse_sse
from tests.test_cancel import GatedRunner


def _wait_status(manager: JobManager, job_id: str, status: str) -> dict[str, Any]:
    """Bounded poll until the job reaches ``status`` (test helper)."""
    for _ in range(500):
        snapshot = manager.status(job_id)
        if snapshot["status"] == status:
            return snapshot
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} never reached {status!r}: {snapshot}")


def test_cancel_while_queued_is_terminal_cancelled() -> None:
    gate = threading.Event()
    runner = GatedRunner(gate)  # holds the only concurrency slot
    manager = JobManager(
        engine=RunEngine(graph_factory=lambda spec: runner),
        max_concurrent=1,
        max_queued=10,
    )
    try:
        running_id = manager.submit_run({"ticker": "NVDA", "date": "2025-01-10"})
        _wait_status(manager, running_id, "running")

        queued_id = manager.submit_run({"ticker": "MSFT", "date": "2025-01-10"})
        assert manager.status(queued_id)["status"] == "queued"

        assert manager.cancel(queued_id) is True
        final = manager.wait(queued_id)
        assert final["status"] == "cancelled"
        assert final["error"] == "Cancelled"
    finally:
        gate.set()
    manager.wait(running_id)


def test_failed_job_stores_category_only() -> None:
    secret_detail = "sqlite lock held by PID 4242 token=hunter2"

    class ExplodingRunner(StubRunner):
        def finalize(self) -> tuple[dict[str, Any], str]:
            raise RuntimeError(secret_detail)

    manager = JobManager(
        engine=RunEngine(
            graph_factory=StubGraphFactory(ExplodingRunner(standard_snapshots(), signal="Buy"))
        )
    )
    job_id = manager.submit_run({"ticker": "NVDA", "date": "2025-01-10"})

    final = manager.wait(job_id)

    assert final["status"] == "failed"
    assert final["error"] == "RuntimeError"
    assert secret_detail not in str(final["error"])


def test_pool_full_rejects_new_submissions() -> None:
    gate = threading.Event()
    runner = GatedRunner(gate)
    manager = JobManager(
        engine=RunEngine(graph_factory=lambda spec: runner),
        max_concurrent=1,
        max_queued=1,
    )
    try:
        running_id = manager.submit_run({"ticker": "NVDA", "date": "2025-01-10"})
        _wait_status(manager, running_id, "running")
        manager.submit_run({"ticker": "MSFT", "date": "2025-01-10"})  # fills the queue
        with pytest.raises(PoolFullError):
            manager.submit_run({"ticker": "AAPL", "date": "2025-01-10"})
    finally:
        gate.set()


def test_subscriber_cap_enforced() -> None:
    gate = threading.Event()
    runner = GatedRunner(gate)
    manager = JobManager(engine=RunEngine(graph_factory=lambda spec: runner), max_subscribers=3)
    try:
        job_id = manager.submit_run({"ticker": "NVDA", "date": "2025-01-10"})
        _wait_status(manager, job_id, "running")
        for _ in range(3):
            manager.subscribe(job_id)
        with pytest.raises(SubscriberLimitError):
            manager.subscribe(job_id)
    finally:
        gate.set()
    manager.wait(job_id)


def test_terminal_job_evicted_after_ttl() -> None:
    manager = JobManager(engine=RunEngine(graph_factory=StubGraphFactory()), job_ttl_seconds=0.05)
    job_id = manager.submit_run({"ticker": "NVDA", "date": "2025-01-10"})
    manager.wait(job_id)

    time.sleep(0.06)  # let the TTL elapse while the job stays terminal
    with pytest.raises(KeyError):
        manager.status("no-such-job")  # sweep runs on public entry points

    with pytest.raises(KeyError):
        manager.status(job_id)  # evicted by the sweep


def test_delete_job_refuses_running_and_evicts_terminal() -> None:
    gate = threading.Event()
    runner = GatedRunner(gate)
    manager = JobManager(engine=RunEngine(graph_factory=lambda spec: runner))
    try:
        job_id = manager.submit_run({"ticker": "NVDA", "date": "2025-01-10"})
        _wait_status(manager, job_id, "running")
        with pytest.raises(JobNotTerminalError):
            manager.delete_job(job_id)
    finally:
        gate.set()
    manager.wait(job_id)

    assert manager.delete_job(job_id) is True
    with pytest.raises(KeyError):
        manager.status(job_id)


def test_provider_lock_timeout_fails_job() -> None:
    manager = JobManager(
        engine=RunEngine(graph_factory=StubGraphFactory()), provider_lock_timeout=0.05
    )
    with manager._provider_lock:  # simulate a stuck provider build
        job_id = manager.submit_run({"ticker": "NVDA", "date": "2025-01-10"})
        for _ in range(500):
            snapshot = manager.status(job_id)
            if snapshot["status"] == "failed":
                break
            time.sleep(0.01)
    assert snapshot["status"] == "failed"
    assert snapshot["error"] == "ProviderLockTimeout"


def test_event_log_cap_trims_oldest_envelopes() -> None:
    # 12 snapshots + statuses + start + decision produce >5 envelopes; the log
    # must keep only the newest max_events while seq stays monotonic.
    runner = StubRunner(standard_snapshots(), signal="Buy")
    manager = JobManager(engine=RunEngine(graph_factory=StubGraphFactory(runner)), max_events=5)

    job_id = manager.submit_run({"ticker": "NVDA", "date": "2025-01-10"})
    manager.wait(job_id)

    assert manager.status(job_id)["event_count"] == 5
    events = list(manager.subscribe(job_id, cursor=0))
    seqs = [envelope["seq"] for envelope in events]
    assert len(seqs) == 5
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)  # unique, monotonic
    assert seqs != [1, 2, 3, 4, 5]  # oldest envelopes were trimmed away
    assert events[-1]["event"]["type"] == "decision"  # newest events survive


def test_subscriber_slot_freed_after_started_generator_closed() -> None:
    gate = threading.Event()

    class GateBlockedRunner(StubRunner):
        """StubRunner whose stream blocks on a gate before every snapshot."""

        def __init__(self, gate: threading.Event) -> None:
            super().__init__(standard_snapshots(), signal="Buy")
            self._gate = gate

        def stream(self) -> Iterator[dict[str, Any]]:
            for snap in self._snapshots:
                self._gate.wait(timeout=5)
                yield snap

    runner = GateBlockedRunner(gate)
    manager = JobManager(engine=RunEngine(graph_factory=lambda spec: runner), max_subscribers=1)
    try:
        job_id = manager.submit_run({"ticker": "NVDA", "date": "2025-01-10"})
        _wait_status(manager, job_id, "running")

        first = manager.subscribe(job_id)
        with pytest.raises(SubscriberLimitError):
            manager.subscribe(job_id)

        gate.set()  # let the run produce live events
        first_event = next(first)  # start the generator so its finally can run
        assert first_event["seq"] == 1
        first.close()  # client-disconnect analog: cleanup must unregister

        second = manager.subscribe(job_id)  # the freed slot accepts a new client
        late_events = list(second)
        assert late_events[-1]["event"]["type"] == "decision"
    finally:
        gate.set()
    manager.wait(job_id)


# ----------------------------- API surface -----------------------------


def test_api_pool_full_returns_429_with_retry_after() -> None:
    gate = threading.Event()
    runner = GatedRunner(gate)
    manager = JobManager(
        engine=RunEngine(graph_factory=lambda spec: runner),
        max_concurrent=1,
        max_queued=1,
    )
    app = create_app(manager=manager)

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.post("/api/runs", json={"ticker": "NVDA", "date": "2025-01-10"})
            assert first.status_code == 202
            job_id = first.json()["job_id"]
            for _ in range(500):
                status = (await client.get(f"/api/runs/{job_id}")).json()
                if status["status"] == "running":
                    break
                await asyncio.sleep(0.01)
            assert status["status"] == "running"

            second = await client.post("/api/runs", json={"ticker": "MSFT", "date": "2025-01-10"})
            assert second.status_code == 202

            third = await client.post("/api/runs", json={"ticker": "AAPL", "date": "2025-01-10"})
            assert third.status_code == 429
            assert third.headers["retry-after"] == "5"

            listed = (await client.get("/api/runs")).json()
            assert {run["ticker"] for run in listed} == {"NVDA", "MSFT"}

    try:
        asyncio.run(scenario())
    finally:
        gate.set()


@pytest.mark.parametrize(
    "body",
    [
        {"ticker": "A" * 17, "date": "2025-01-10"},  # ticker too long
        {"ticker": "BRK.B", "date": "01/10/2025"},  # bad date format
        {"ticker": "NVDA", "date": "2025-01-10", "provider": "nope"},  # unknown provider
        {
            "ticker": "NVDA",
            "date": "2025-01-10",
            "provider": "direct",
            "instructions": "focus on margins",
        },  # instructions + direct
        {
            "ticker": "NVDA",
            "date": "2025-01-10",
            "provider": "ta_plugins",
            "instructions": "x" * 4001,
        },  # instructions too long
        {
            "ticker": "NVDA",
            "date": "2025-01-10",
            "provider": "ta_plugins",
            "instructions": "focus\x00 on margins",
        },  # control characters in instructions
    ],
)
def test_api_invalid_run_requests_are_422_static(body: dict[str, Any]) -> None:
    app = create_app(engine=RunEngine(graph_factory=StubGraphFactory()))

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/runs", json=body)
            assert response.status_code == 422
            assert response.json()["detail"] == "invalid run request"

    asyncio.run(scenario())


@pytest.mark.parametrize("bad", ["junk", "", "12.5", "9" * 19])
def test_api_malformed_last_event_id_is_400(bad: str) -> None:
    manager = JobManager(engine=RunEngine(graph_factory=StubGraphFactory()))
    app = create_app(manager=manager)
    job_id = manager.submit_run({"ticker": "NVDA", "date": "2025-01-10"})
    manager.wait(job_id)

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/api/runs/{job_id}/events",
                headers={"Last-Event-ID": bad},
            )
            assert response.status_code == 400
            # Malformed header wins over a supplied cursor: never fall back silently.
            guarded = await client.get(
                f"/api/runs/{job_id}/events",
                params={"cursor": 3},
                headers={"Last-Event-ID": bad},
            )
            assert guarded.status_code == 400

    asyncio.run(scenario())


def test_api_valid_last_event_id_takes_precedence_over_cursor() -> None:
    manager = JobManager(engine=RunEngine(graph_factory=StubGraphFactory()))
    app = create_app(manager=manager)
    job_id = manager.submit_run({"ticker": "NVDA", "date": "2025-01-10"})
    manager.wait(job_id)

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream(
                "GET",
                f"/api/runs/{job_id}/events",
                params={"cursor": 9},
                headers={"Last-Event-ID": "1"},
            ) as response:
                assert response.status_code == 200
                events = parse_sse((await response.aread()).decode())
            assert events[0]["data"]["seq"] == 2  # header cursor=1 wins over ?cursor=9

    asyncio.run(scenario())


def test_api_delete_running_cancels_and_terminal_evicts() -> None:
    gate = threading.Event()
    runner = GatedRunner(gate)
    manager = JobManager(engine=RunEngine(graph_factory=lambda spec: runner))
    app = create_app(manager=manager)

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/runs", json={"ticker": "NVDA", "date": "2025-01-10"})
            job_id = created.json()["job_id"]
            for _ in range(500):
                status = (await client.get(f"/api/runs/{job_id}")).json()
                if status["status"] == "running":
                    break
                await asyncio.sleep(0.01)

            refused = await client.delete(f"/api/runs/{job_id}")
            assert refused.status_code == 200
            assert refused.json() == {"job_id": job_id, "deleted": False, "cancelled": True}
            gate.set()  # release the stream only after the running DELETE was refused

            for _ in range(500):
                status = (await client.get(f"/api/runs/{job_id}")).json()
                if status["status"] == "cancelled":
                    break
                await asyncio.sleep(0.01)
            assert status["status"] == "cancelled"

            evicted = await client.delete(f"/api/runs/{job_id}")
            assert evicted.status_code == 200
            assert evicted.json() == {"job_id": job_id, "deleted": True, "cancelled": False}
            assert (await client.get(f"/api/runs/{job_id}")).status_code == 404

    asyncio.run(scenario())


def test_api_subscriber_cap_returns_429() -> None:
    # httpx ASGITransport buffers each request to completion, so a held-open
    # SSE request cannot coexist with a second HTTP request; occupy the cap
    # slot directly at the manager level instead (registration is eager).
    manager = JobManager(engine=RunEngine(graph_factory=StubGraphFactory()), max_subscribers=1)
    app = create_app(manager=manager)
    job_id = manager.submit_run({"ticker": "NVDA", "date": "2025-01-10"})
    manager.wait(job_id)
    occupied = manager.subscribe(job_id)  # slot 1: never iterated, stays registered

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            second = await client.get(f"/api/runs/{job_id}/events")
            assert second.status_code == 429
            assert second.headers["retry-after"] == "5"

    try:
        asyncio.run(scenario())
    finally:
        occupied.close()  # free the slot so the generator's finally unregister runs
