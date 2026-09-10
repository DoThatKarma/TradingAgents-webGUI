"""Cooperative cancellation between stream chunks (ADR 0003: offline only)."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest

from app.engine.runner import RunCancelled, RunEngine, RunSpec
from app.jobs.manager import JobManager
from tests.stubs import standard_snapshots


class GatedRunner:
    """GraphRunner whose stream blocks on a gate before every snapshot."""

    def __init__(self, gate: threading.Event) -> None:
        self._gate = gate
        self._snapshots = standard_snapshots()
        self.stream_closed = False

    def stream(self) -> Iterator[dict[str, Any]]:
        try:
            for snap in self._snapshots:
                self._gate.wait(timeout=5)
                yield snap
        finally:
            self.stream_closed = True

    def finalize(self) -> tuple[dict[str, Any], str]:
        raise AssertionError("a cancelled run must never reach finalize")


def test_cancel_stops_job_between_chunks() -> None:
    gate = threading.Event()
    runner = GatedRunner(gate)
    manager = JobManager(engine=RunEngine(graph_factory=lambda spec: runner))

    job_id = manager.submit_run({"ticker": "NVDA", "date": "2025-01-10"})
    for _ in range(500):
        if manager.status(job_id)["status"] == "running":
            break
        time.sleep(0.01)
    assert manager.cancel(job_id) is True
    gate.set()  # release the stream so the engine observes the cancel flag

    final = manager.wait(job_id)
    assert final["status"] == "cancelled"
    assert runner.stream_closed
    assert manager.cancel(job_id) is False  # already terminal


def test_engine_raises_run_cancelled_between_chunks() -> None:
    gate = threading.Event()
    gate.set()
    runner = GatedRunner(gate)
    cancelled = threading.Event()
    cancelled.set()
    engine = RunEngine(graph_factory=lambda spec: runner)

    iterator = engine.run(RunSpec(ticker="NVDA", date="2025-01-10"), should_cancel=cancelled.is_set)

    with pytest.raises(RunCancelled):
        list(iterator)
    assert runner.stream_closed
