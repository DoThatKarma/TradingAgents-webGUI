"""Replay: late subscribers receive full history; cursors skip seen events."""

from __future__ import annotations

import pytest

from app.engine.runner import RunEngine
from app.jobs.manager import JobManager
from tests.stubs import StubGraphFactory


def _completed_manager() -> tuple[JobManager, str]:
    manager = JobManager(engine=RunEngine(graph_factory=StubGraphFactory()))
    job_id = manager.submit_run({"ticker": "NVDA", "date": "2025-01-10"})
    manager.wait(job_id)
    return manager, job_id


def test_late_subscriber_gets_full_history_from_cursor_zero() -> None:
    manager, job_id = _completed_manager()

    events = list(manager.subscribe(job_id, cursor=0))

    assert events
    assert [envelope["seq"] for envelope in events] == list(range(1, len(events) + 1))
    assert events[-1]["event"]["type"] == "decision"


def test_cursor_skips_already_seen_events() -> None:
    manager, job_id = _completed_manager()

    full = list(manager.subscribe(job_id, cursor=0))
    resumed = list(manager.subscribe(job_id, cursor=2))

    assert [envelope["seq"] for envelope in resumed] == [
        envelope["seq"] for envelope in full if envelope["seq"] > 2
    ]


def test_subscribe_unknown_job_raises_keyerror() -> None:
    manager = JobManager(engine=RunEngine(graph_factory=StubGraphFactory()))

    with pytest.raises(KeyError):
        manager.subscribe("no-such-job")
