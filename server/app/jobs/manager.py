"""Background job manager: worker threads + replayable, multi-subscriber event log.

Concurrency contract
--------------------
Instruction-provider application is process-global: the ``ta_plugins`` adapter
applies plugins via ``plugin_scope``, which patches class-level agent
factories for the whole process while a runner is being built. Jobs therefore
acquire a single provider lock around runner construction (``RunEngine.run``
invokes the graph factory eagerly), so providers are used strictly
sequentially. Streaming itself runs concurrently. Full multi-user isolation
requires one worker process per run and is deliberately deferred.

Provider-lock timeout (hardening)
---------------------------------
The provider lock is acquired with a bounded ``provider_lock_timeout``
(default 120s). A job whose runner cannot be constructed within that window
fails with the error category ``ProviderLockTimeout`` instead of blocking a
worker thread forever.

Resource limits (hardening)
---------------------------
- ``max_concurrent`` bounds simultaneously *running* jobs via a semaphore;
  ``max_queued`` bounds jobs waiting beyond that. When the combined pool
  (``max_concurrent + max_queued``) is full, :meth:`submit_run` raises
  :class:`PoolFullError` immediately (the API maps it to ``429`` with
  ``Retry-After``).
- ``max_events`` caps the per-job replay log; the oldest envelopes are
  trimmed first. ``seq`` values stay unique and monotonically increasing,
  but once trimming occurred, a cursor older than the oldest retained
  envelope replays only from that envelope — earlier events are
  unrecoverable.
- Terminal jobs are evicted after ``job_ttl_seconds`` by a sweep that runs
  on ``submit_run``/``status``/``list_jobs`` calls (no timer thread).
- ``max_subscribers`` caps concurrent SSE subscribers per job; excess
  subscribers are rejected with :class:`SubscriberLimitError` (``429``).
- Error hygiene: job errors store a short category only (exception class
  name or ``Cancelled``/``ProviderLockTimeout``); full tracebacks go to the
  server log, never to clients.
"""

from __future__ import annotations

import itertools
import logging
import threading
import time
import unicodedata
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from queue import Empty, Queue
from typing import Any

from app.engine.runner import RunCancelled, RunEngine, RunSpec

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = frozenset({"completed", "cancelled", "failed"})
_VALID_PROVIDERS = frozenset({"direct", "ta_plugins"})
_VALID_ASSET_TYPES = frozenset({"stock", "crypto", "polymarket"})


def validate_instructions(instructions: Any) -> None:
    """Reject control characters in custom instructions (hardening, shared rule).

    Single source of truth used by both the job manager and the API layer so
    non-HTTP callers cannot bypass the boundary. Allowed whitespace: ``\n``,
    ``\r``, ``\t`` (legitimate prompt formatting); every other C0/C1 control
    character is rejected with a static message (no input reflection).
    """
    if not instructions:
        return
    for char in instructions:
        if char in "\n\r\t":
            continue  # legitimate prompt formatting
        if unicodedata.category(char) == "Cc":  # C0, DEL, C1 controls
            raise ValueError("instructions contain invalid control characters")


class PoolFullError(RuntimeError):
    """The job pool (running + queued) is at capacity."""


class SubscriberLimitError(RuntimeError):
    """The per-job subscriber cap is reached."""


class JobNotTerminalError(RuntimeError):
    """Deletion was requested for a job that is still running or queued."""


class ProviderLockTimeoutError(RuntimeError):
    """The provider lock could not be acquired within the timeout."""

    category = "ProviderLockTimeout"


def _error_category(exc: BaseException) -> str:
    """Classify an exception into the short, client-visible error category.

    Error-hygiene contract: categories only — the full message stays in the
    server log (see module docstring), never in the job snapshot. Explicit
    ``category`` attributes win (e.g. ``ProviderLockTimeout``). ``ValueError``
    messages mentioning the API key collapse into ``MissingApiKey`` so a
    misconfigured deployment surfaces an actionable hint instead of a raw
    provider error name; the provider-specific detail remains in the log.
    """
    explicit = getattr(exc, "category", None)
    if explicit:
        return str(explicit)
    if isinstance(exc, ValueError) and "api key" in str(exc).lower():
        return "MissingApiKey"
    return type(exc).__name__


@dataclass
class _Subscriber:
    """One SSE follower: an event queue plus a terminal-state signal."""

    queue: Queue
    done: threading.Event


@dataclass
class _Job:
    id: str
    spec: RunSpec
    status: str = "queued"
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    terminal_at: float | None = None
    next_seq: int = 1
    lock: threading.Lock = field(default_factory=threading.Lock)
    cancel: threading.Event = field(default_factory=threading.Event)
    events: list[dict[str, Any]] = field(default_factory=list)
    subscribers: dict[int, _Subscriber] = field(default_factory=dict)
    sub_ids: Iterator[int] = field(default_factory=itertools.count)


class JobManager:
    """Runs analyses in worker threads behind a replayable event log.

    Event log contract: append-only per run; every envelope carries a
    1-based, monotonically increasing ``seq``; subscribers each start at
    their own ``cursor`` (last seq seen) and replay everything after it —
    late joiners pass 0 and receive the retained history, so multiple
    browser tabs can share one run safely. Once the log exceeds
    ``max_events``, oldest envelopes are trimmed and replay can only start
    at the oldest retained envelope (see module docstring).
    """

    def __init__(
        self,
        engine: RunEngine | None = None,
        *,
        max_concurrent: int = 3,
        max_queued: int = 50,
        max_events: int = 10000,
        job_ttl_seconds: float = 3600.0,
        max_subscribers: int = 10,
        provider_lock_timeout: float = 120.0,
    ) -> None:
        self._engine = engine or RunEngine()
        self._jobs: dict[str, _Job] = {}
        self._registry_lock = threading.Lock()
        self._provider_lock = threading.Lock()
        self._max_active = max_concurrent + max_queued
        self._slots = threading.BoundedSemaphore(max_concurrent)
        self._max_events = max_events
        self._job_ttl = job_ttl_seconds
        self._max_subscribers = max_subscribers
        self._provider_lock_timeout = provider_lock_timeout

    def submit_run(self, config: dict[str, Any]) -> str:
        """Create a job from a run config dict and start its worker thread.

        Fail-fast validation raises :class:`ValueError`; a saturated pool
        raises :class:`PoolFullError` (API maps both to typed HTTP errors).
        """
        try:
            ticker = str(config["ticker"]).strip()
            date = str(config["date"]).strip()
        except KeyError as exc:
            raise ValueError(f"missing required field: {exc}") from exc
        if not ticker or not date:
            raise ValueError("ticker and date must be non-empty")
        asset_type = config.get("asset_type", "stock")
        provider = config.get("provider", "direct")
        instructions = config.get("instructions")
        if asset_type not in _VALID_ASSET_TYPES:
            raise ValueError(f"unknown asset_type {asset_type!r}")
        if provider not in _VALID_PROVIDERS:
            raise ValueError(f"unknown provider {provider!r}")
        if provider == "direct" and instructions:
            raise ValueError("instructions require provider 'ta_plugins'")
        validate_instructions(instructions)
        self._sweep()
        job_id = uuid.uuid4().hex
        job = _Job(
            id=job_id,
            spec=RunSpec(
                ticker=ticker,
                date=date,
                asset_type=asset_type,
                instructions=instructions,
                provider=provider,
            ),
        )
        with self._registry_lock:
            active = sum(1 for existing in self._jobs.values() if existing.terminal_at is None)
            if active >= self._max_active:
                raise PoolFullError(f"job pool full ({active} active jobs)")
            self._jobs[job_id] = job
        threading.Thread(
            target=self._execute, args=(job,), name=f"ta-run-{job_id[:8]}", daemon=True
        ).start()
        return job_id

    def status(self, job_id: str) -> dict[str, Any]:
        """Return a snapshot of the job state."""
        self._sweep()
        job = self._require(job_id)
        with job.lock:
            return self._snapshot(job)

    def list_jobs(self) -> list[dict[str, Any]]:
        """Return status snapshots for all non-evicted jobs (creation order)."""
        self._sweep()
        with self._registry_lock:
            jobs = sorted(self._jobs.values(), key=lambda job: job.created_at)
        return [self._snapshot(job) for job in jobs]

    def delete_job(self, job_id: str) -> bool:
        """Evict a terminal job from the registry.

        Refuses non-terminal jobs with :class:`JobNotTerminalError`; unknown
        ids raise :class:`KeyError`.
        """
        job = self._require(job_id)
        with job.lock:
            if job.status not in _TERMINAL_STATUSES:
                raise JobNotTerminalError(f"job {job_id} is not terminal yet")
        with self._registry_lock:
            self._jobs.pop(job_id, None)
        return True

    def cancel(self, job_id: str) -> bool:
        """Request cooperative cancellation; False when already terminal.

        The engine checks the cancel flag between stream chunks, so a run in
        its final steps may still complete instead of being cancelled.
        """
        job = self._require(job_id)
        with job.lock:
            if job.status in _TERMINAL_STATUSES:
                return False
            job.cancel.set()
            if job.status == "queued":
                job.status = "cancelled"
                job.error = "Cancelled"
                job.terminal_at = time.time()
                for subscriber in job.subscribers.values():
                    subscriber.done.set()
            return True

    def subscribe(self, job_id: str, cursor: int = 0) -> Iterator[dict[str, Any]]:
        """Yield event envelopes with ``seq`` > ``cursor`` until the job ends."""
        job = self._require(job_id)
        queue: Queue = Queue()
        done = threading.Event()
        with job.lock:
            if len(job.subscribers) >= self._max_subscribers:
                raise SubscriberLimitError(f"job {job_id} subscriber cap reached")
            events_ref = job.events  # reference only; filtered/copied below (M4)
            high_water = job.next_seq  # events from here on arrive via live push
            sub_id = next(job.sub_ids)
            subscriber = _Subscriber(queue=queue, done=done)
            job.subscribers[sub_id] = subscriber
            if job.status in _TERMINAL_STATUSES:
                done.set()
        # Copy/filter outside the lock (M4). Events with seq < high_water are
        # never live-pushed, so replay and live delivery cannot duplicate.
        for envelope in list(events_ref):
            if cursor < envelope["seq"] < high_water:
                queue.put(envelope)
        return self._follow(job, sub_id, queue, done)

    def wait(self, job_id: str, timeout: float = 30.0) -> dict[str, Any]:
        """Block until the job reaches a terminal status (CLI/test helper)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            snapshot = self.status(job_id)
            if snapshot["status"] in _TERMINAL_STATUSES:
                return snapshot
            time.sleep(0.01)
        raise TimeoutError(f"job {job_id} did not finish within {timeout}s")

    def _require(self, job_id: str) -> _Job:
        with self._registry_lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)
            return self._jobs[job_id]

    def _sweep(self) -> None:
        """Evict terminal jobs past their TTL (called on public entry points)."""
        now = time.time()
        with self._registry_lock:
            expired = [
                job_id
                for job_id, job in self._jobs.items()
                if job.terminal_at is not None and now - job.terminal_at >= self._job_ttl
            ]
            for job_id in expired:
                del self._jobs[job_id]

    @staticmethod
    def _snapshot(job: _Job) -> dict[str, Any]:
        # Caller holds job.lock.
        return {
            "job_id": job.id,
            "ticker": job.spec.ticker,
            "date": job.spec.date,
            "asset_type": job.spec.asset_type,
            "provider": job.spec.provider,
            "effective_provider": job.spec.effective_provider,
            "has_instructions": job.spec.instructions is not None,
            "status": job.status,
            "created_at": job.created_at,
            "error": job.error,
            "event_count": len(job.events),
        }

    def _execute(self, job: _Job) -> None:
        self._slots.acquire()  # blocks while max_concurrent runs are active
        try:
            with job.lock:
                if job.status == "cancelled":  # cancelled while queued
                    return
                job.status = "running"
            terminal = "completed"
            try:
                # Provider application is process-global (see module
                # docstring): construct the runner under the provider lock,
                # bounded by the lock timeout, and stream outside it.
                acquired = self._provider_lock.acquire(timeout=self._provider_lock_timeout)
                if not acquired:
                    raise ProviderLockTimeoutError(
                        f"provider lock not acquired within {self._provider_lock_timeout}s"
                    )
                try:
                    events = self._engine.run(job.spec, should_cancel=job.cancel.is_set)
                finally:
                    self._provider_lock.release()
                for event in events:
                    self._append(job, event)
            except RunCancelled:
                terminal = "cancelled"
            except Exception as exc:  # workers surface categories, never crash
                # M1: log details server-side; store only a short category.
                logger.exception("job %s failed with %s", job.id, type(exc).__name__)
                with job.lock:
                    job.error = _error_category(exc)
                terminal = "failed"
            self._finish(job, terminal)
        finally:
            self._slots.release()

    def _append(self, job: _Job, event: dict[str, Any]) -> None:
        with job.lock:
            envelope = {"seq": job.next_seq, "ts": time.time(), "event": event}
            job.next_seq += 1
            job.events.append(envelope)
            if len(job.events) > self._max_events:
                del job.events[: len(job.events) - self._max_events]
            for subscriber in job.subscribers.values():
                subscriber.queue.put(envelope)

    def _finish(self, job: _Job, status: str) -> None:
        with job.lock:
            job.status = status
            if status == "cancelled" and job.error is None:
                job.error = "Cancelled"
            job.terminal_at = time.time()
            for subscriber in job.subscribers.values():
                subscriber.done.set()

    @staticmethod
    def _follow(
        job: _Job, sub_id: int, queue: Queue, done: threading.Event
    ) -> Iterator[dict[str, Any]]:
        try:
            while True:
                if done.is_set():
                    # Terminal: drain what is queued, then stop.
                    while True:
                        try:
                            item = queue.get_nowait()
                        except Empty:
                            return
                        yield item
                try:
                    item = queue.get(timeout=1.0)
                except Empty:
                    # Periodic wakeup keeps this generator interruptible so a
                    # client disconnect can still close and unregister it.
                    continue
                yield item
        finally:
            with job.lock:
                job.subscribers.pop(sub_id, None)
