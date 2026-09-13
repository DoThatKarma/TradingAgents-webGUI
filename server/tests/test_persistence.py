"""Disk persistence of finished runs (ADR 0008): save, restore, TTL exemption.

All offline (ADR 0003): canned stub runs, ``tmp_path`` storage, an in-process
restart (a fresh JobManager over the same directory), and ASGI transport for
the HTTP-level contract — no network and no API keys.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

import httpx

from app.api.app import create_app
from app.engine.runner import RunEngine
from app.jobs.manager import JobManager
from app.jobs.persistence import RunStore, redact
from tests.stubs import StubGraphFactory, StubRunner, standard_snapshots


def _engine() -> RunEngine:
    return RunEngine(graph_factory=StubGraphFactory())


def _run_one(manager: JobManager) -> str:
    job_id = manager.submit_run({"ticker": "NVDA", "date": "2025-01-10"})
    snapshot = manager.wait(job_id)
    assert snapshot["status"] == "completed"
    return job_id


def _jobs_dir(root: Path) -> Path:
    return root / "jobs"


def test_completed_job_is_persisted_with_events_and_decision(tmp_path: Path) -> None:
    manager = JobManager(engine=_engine(), persist_dir=tmp_path)

    job_id = _run_one(manager)

    path = _jobs_dir(tmp_path) / f"{job_id}.json"
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema"] == 1
    job = payload["job"]
    assert job["id"] == job_id
    assert job["status"] == "completed"
    assert job["spec"]["ticker"] == "NVDA"
    assert job["spec"]["date"] == "2025-01-10"
    assert job["created_at"] > 0 and job["terminal_at"] >= job["created_at"]
    kinds = [envelope["event"]["type"] for envelope in job["events"]]
    assert "decision" in kinds
    assert "report" in kinds
    decision = next(env["event"] for env in job["events"] if env["event"]["type"] == "decision")
    assert decision["signal"] == "Buy"


def test_restart_restores_status_report_and_replay(tmp_path: Path) -> None:
    first = JobManager(engine=_engine(), persist_dir=tmp_path)
    job_id = _run_one(first)

    restarted = JobManager(engine=_engine(), persist_dir=tmp_path)

    snapshot = restarted.status(job_id)
    assert snapshot["status"] == "completed"
    assert snapshot["ticker"] == "NVDA"
    listed = [job["job_id"] for job in restarted.list_jobs()]
    assert listed == [job_id]
    material = restarted.export_report_material(job_id)
    assert material["ticker"] == "NVDA"
    assert "market_report" in material["reports"]
    assert material["decision"] is not None
    replay = list(restarted.subscribe(job_id, cursor=0))
    assert replay
    assert replay[-1]["event"]["type"] == "decision"


def test_restored_jobs_are_not_reexecuted(tmp_path: Path) -> None:
    runner = StubRunner(standard_snapshots(), signal="Buy")
    factory = StubGraphFactory(runner)
    first = JobManager(engine=RunEngine(graph_factory=factory), persist_dir=tmp_path)
    job_id = _run_one(first)
    finalize_calls_after_first = runner.finalize_calls

    restarted = JobManager(engine=RunEngine(graph_factory=factory), persist_dir=tmp_path)

    assert restarted.status(job_id)["status"] == "completed"
    assert runner.finalize_calls == finalize_calls_after_first
    assert factory.specs == [factory.specs[0]]  # no second runner construction


def test_persisted_jobs_exempt_from_ttl_sweep(tmp_path: Path) -> None:
    manager = JobManager(engine=_engine(), persist_dir=tmp_path, job_ttl_seconds=0.0)
    job_id = _run_one(manager)

    # ttl=0 would evict any terminal job instantly without persistence;
    # persisted finished jobs stay served (ADR 0008 exemption).
    for _ in range(3):
        assert manager.status(job_id)["status"] == "completed"
    assert (_jobs_dir(tmp_path) / f"{job_id}.json").is_file()


def test_delete_job_removes_disk_snapshot(tmp_path: Path) -> None:
    manager = JobManager(engine=_engine(), persist_dir=tmp_path)
    job_id = _run_one(manager)
    path = _jobs_dir(tmp_path) / f"{job_id}.json"
    assert path.is_file()

    assert manager.delete_job(job_id) is True

    assert not path.exists()
    restarted = JobManager(engine=_engine(), persist_dir=tmp_path)
    try:
        restarted.status(job_id)
        raise AssertionError("deleted job must not be restored")
    except KeyError:
        pass


class _BlockingRunner:
    """Runner whose stream blocks until released (running-state control)."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def stream(self) -> Any:
        self.started.set()
        assert self.release.wait(timeout=10.0)
        yield {}

    def finalize(self) -> tuple[dict[str, Any], str]:
        return {}, "Hold"


def test_running_job_not_persisted_until_terminal(tmp_path: Path) -> None:
    runner = _BlockingRunner()
    manager = JobManager(
        engine=RunEngine(graph_factory=StubGraphFactory(runner)), persist_dir=tmp_path
    )
    job_id = manager.submit_run({"ticker": "NVDA", "date": "2025-01-10"})

    assert runner.started.wait(timeout=5.0)
    for _ in range(500):
        if manager.status(job_id)["status"] == "running":
            break
    assert manager.status(job_id)["status"] == "running"
    assert not (_jobs_dir(tmp_path) / f"{job_id}.json").exists()

    runner.release.set()
    manager.wait(job_id)
    assert (_jobs_dir(tmp_path) / f"{job_id}.json").is_file()


class _BoomRunner:
    def stream(self) -> Any:
        raise RuntimeError("boom with api key detail")
        yield {}  # pragma: no cover - makes this a generator

    def finalize(self) -> tuple[dict[str, Any], str]:  # pragma: no cover
        return {}, "Hold"


def test_failed_job_persists_category_only(tmp_path: Path) -> None:
    manager = JobManager(
        engine=RunEngine(graph_factory=StubGraphFactory(_BoomRunner())), persist_dir=tmp_path
    )
    job_id = manager.submit_run({"ticker": "NVDA", "date": "2025-01-10"})

    snapshot = manager.wait(job_id)

    assert snapshot["status"] == "failed"
    raw = (_jobs_dir(tmp_path) / f"{job_id}.json").read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert payload["job"]["error"] == "RuntimeError"
    assert "boom" not in raw  # message stays in the server log only
    assert "Traceback" not in raw


def test_cancelled_while_queued_job_is_persisted(tmp_path: Path) -> None:
    blocker = _BlockingRunner()
    manager = JobManager(
        engine=RunEngine(graph_factory=StubGraphFactory(blocker)),
        persist_dir=tmp_path,
        max_concurrent=1,
    )
    running_id = manager.submit_run({"ticker": "MSFT", "date": "2025-01-10"})
    assert blocker.started.wait(timeout=5.0)

    queued_id = manager.submit_run({"ticker": "NVDA", "date": "2025-01-10"})
    assert manager.cancel(queued_id) is True
    snapshot = manager.wait(queued_id)

    assert snapshot["status"] == "cancelled"
    payload = json.loads((_jobs_dir(tmp_path) / f"{queued_id}.json").read_text(encoding="utf-8"))
    assert payload["job"]["status"] == "cancelled"
    assert payload["job"]["error"] == "Cancelled"
    blocker.release.set()
    manager.wait(running_id)


def test_corrupt_and_foreign_files_skipped(tmp_path: Path, caplog: Any) -> None:
    good = JobManager(engine=_engine(), persist_dir=tmp_path)
    good_id = _run_one(good)
    jobs_dir = _jobs_dir(tmp_path)
    (jobs_dir / "deadbeefdeadbeefdeadbeefdeadbeef.json").write_text("{not json", encoding="utf-8")
    (jobs_dir / "feedfacefeedfacefeedfacefeedface.json").write_text(
        json.dumps({"hello": "world"}), encoding="utf-8"
    )

    restarted = JobManager(engine=_engine(), persist_dir=tmp_path)

    restored = [job["job_id"] for job in restarted.list_jobs()]
    assert restored == [good_id]
    assert restarted.export_report_material(good_id)["ticker"] == "NVDA"
    assert any("skipping" in record.getMessage() for record in caplog.records)


def test_no_files_written_without_persist_dir(tmp_path: Path) -> None:
    marker = tmp_path / "nowhere"
    marker.mkdir()
    manager = JobManager(engine=_engine())  # persist_dir unset: today's behavior

    job_id = _run_one(manager)

    assert manager.status(job_id)["status"] == "completed"
    assert list(marker.iterdir()) == []  # nothing created anywhere


def test_atomic_writes_leave_no_tmp_leftovers(tmp_path: Path) -> None:
    manager = JobManager(engine=_engine(), persist_dir=tmp_path)
    _run_one(manager)

    leftovers = [p.name for p in _jobs_dir(tmp_path).iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_secret_looking_events_scrubbed_on_save(tmp_path: Path) -> None:
    scrubbed = redact(
        {"api_key": "sk-live-123", "nested": {"AUTHORIZATION": "Bearer x"}, "content": "ok"}
    )
    assert scrubbed == {
        "api_key": "***redacted***",
        "nested": {"AUTHORIZATION": "***redacted***"},
        "content": "ok",
    }

    store = RunStore(tmp_path)
    store.save(
        job_id="a" * 32,
        spec={"ticker": "NVDA", "date": "2025-01-10"},
        status="completed",
        error=None,
        created_at=1.0,
        terminal_at=2.0,
        events=[{"seq": 1, "ts": 1.5, "event": {"type": "debug", "api_key": "sk-leak"}}],
    )

    raw = (_jobs_dir(tmp_path) / ("a" * 32 + ".json")).read_text(encoding="utf-8")
    assert "sk-leak" not in raw
    assert "***redacted***" in raw


def test_api_lists_restored_runs_and_serves_report(tmp_path: Path) -> None:
    first = JobManager(engine=_engine(), persist_dir=tmp_path)
    job_id = _run_one(first)

    restarted = JobManager(engine=_engine(), persist_dir=tmp_path)
    app = create_app(manager=restarted)

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            runs = await client.get("/api/runs")
            assert runs.status_code == 200
            assert [job["job_id"] for job in runs.json()] == [job_id]

            status = await client.get(f"/api/runs/{job_id}")
            assert status.status_code == 200
            assert status.json()["status"] == "completed"

            report = await client.get(f"/api/runs/{job_id}/report")
            assert report.status_code == 200
            assert report.headers["content-type"].startswith("text/markdown")
            assert "NVDA" in report.text
            assert "Final Decision" in report.text
            assert "Buy" in report.text

    asyncio.run(scenario())


def test_env_var_wiring(tmp_path: Path, monkeypatch: Any) -> None:
    persist_root = tmp_path / "data"
    monkeypatch.setenv("TA_WEBGUI_PERSIST_DIR", str(persist_root))
    app = create_app(engine=_engine())

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/runs", json={"ticker": "NVDA", "date": "2025-01-10"})
            assert created.status_code == 202
            job_id = created.json()["job_id"]
            for _ in range(500):
                status = (await client.get(f"/api/runs/{job_id}")).json()
                if status["status"] == "completed":
                    break
            assert status["status"] == "completed"
            return job_id

    job_id = asyncio.run(scenario())
    assert (persist_root / "jobs" / f"{job_id}.json").is_file()

    # Unset (or empty) → zero-risk default: nothing on disk at all.
    monkeypatch.delenv("TA_WEBGUI_PERSIST_DIR")
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    monkeypatch.chdir(empty_root)
    app_off = create_app(engine=_engine())

    async def scenario_off() -> None:
        transport = httpx.ASGITransport(app=app_off)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/runs", json={"ticker": "NVDA", "date": "2025-01-10"})
            assert created.status_code == 202
            off_id = created.json()["job_id"]
            for _ in range(500):
                status = (await client.get(f"/api/runs/{off_id}")).json()
                if status["status"] == "completed":
                    break
            assert status["status"] == "completed"

    asyncio.run(scenario_off())
    assert list(empty_root.iterdir()) == []
