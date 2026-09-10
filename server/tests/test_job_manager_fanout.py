"""JobManager multi-subscriber fan-out: every subscriber sees the full sequence."""

from __future__ import annotations

from app.engine.runner import RunEngine
from app.jobs.manager import JobManager
from tests.stubs import StubGraphFactory


def test_two_subscribers_receive_identical_full_sequence() -> None:
    manager = JobManager(engine=RunEngine(graph_factory=StubGraphFactory()))

    job_id = manager.submit_run({"ticker": "NVDA", "date": "2025-01-10"})
    subscriber_a = manager.subscribe(job_id)
    subscriber_b = manager.subscribe(job_id)
    manager.wait(job_id)

    events_a = list(subscriber_a)
    events_b = list(subscriber_b)

    assert events_a == events_b
    seqs = [envelope["seq"] for envelope in events_a]
    assert seqs == list(range(1, len(seqs) + 1))
    assert events_a[-1]["event"]["type"] == "decision"


def test_subscriber_added_mid_run_still_gets_full_history() -> None:
    manager = JobManager(engine=RunEngine(graph_factory=StubGraphFactory()))

    job_id = manager.submit_run({"ticker": "NVDA", "date": "2025-01-10"})
    # First subscriber rides the live stream; the second joins after completion.
    subscriber_live = manager.subscribe(job_id)
    manager.wait(job_id)
    subscriber_late = manager.subscribe(job_id)

    events_live = list(subscriber_live)
    events_late = list(subscriber_late)

    assert events_late == events_live
    assert events_late[-1]["event"]["type"] == "decision"
