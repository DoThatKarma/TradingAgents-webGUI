"""Durable persistence for finished runs (ADR 0008).

When a persist directory is configured, every job that reaches a terminal
state (completed / failed / cancelled) is written to
``<dir>/jobs/<job_id>.json`` atomically: JSON is dumped to a ``.tmp`` file,
fsynced, then moved into place with :func:`os.replace` — readers never see
partial files and no ``.tmp`` leftovers remain.

Loading is tolerant by contract: corrupt, truncated, or foreign files are
skipped with a single server-side warning line and never crash startup or
lookups. Only payloads passing schema/shape validation are returned.

Privacy contract: payloads carry analysis content (agent status, reports,
decisions), run spec metadata, and short error categories — never API keys
and never tracebacks. As defense in depth, :func:`redact` scrubs values
under secret-looking key names before anything reaches disk.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
_JOBS_SUBDIR = "jobs"
_TERMINAL_STATUSES = frozenset({"completed", "cancelled", "failed"})
# Job ids are manager-generated lowercase hex uuids; the accepted range is
# deliberately narrow so ids never form paths or escape the jobs directory.
_JOB_ID_PATTERN = re.compile(r"^[0-9a-f]{8,64}$")
_SECRET_KEY_PATTERN = re.compile(
    r"api[_-]?key|secret|token|password|passphrase|authorization|credential",
    re.IGNORECASE,
)
_REDACTED = "***redacted***"


def redact(value: Any) -> Any:
    """Return a copy of ``value`` with secret-looking dict values replaced.

    Engine events carry analysis content by construction; should a future
    event kind embed a key-named field, its value is scrubbed before disk.
    Originals are never mutated; scalars pass through untouched.
    """
    if isinstance(value, dict):
        return {
            key: (
                _REDACTED
                if isinstance(key, str) and _SECRET_KEY_PATTERN.search(key)
                else redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


class RunStore:
    """Atomic JSON file store for terminal run snapshots under ``<root>/jobs``.

    The store knows nothing about threads or the job registry: the manager
    serializes writes per job (each terminal transition happens once, under
    the job lock) and calls into this class for save/load/delete only.
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._jobs_dir = Path(root).expanduser() / _JOBS_SUBDIR
        self._jobs_dir.mkdir(parents=True, exist_ok=True)

    @property
    def jobs_dir(self) -> Path:
        """Directory holding the ``<job_id>.json`` snapshots."""
        return self._jobs_dir

    def save(
        self,
        *,
        job_id: str,
        spec: dict[str, Any],
        status: str,
        error: str | None,
        created_at: float,
        terminal_at: float,
        events: list[dict[str, Any]],
    ) -> Path:
        """Atomically write one terminal job snapshot; returns the file path."""
        payload = {
            "schema": SCHEMA_VERSION,
            "job": {
                "id": job_id,
                "spec": spec,
                "status": status,
                "error": error,
                "created_at": created_at,
                "terminal_at": terminal_at,
                "events": redact(events),
            },
        }
        path = self._path(job_id)
        # Unique tmp name: two writers racing on the same job (e.g. two
        # manager instances over one directory in tests) never interleave
        # on a shared temp file; os.replace still leaves no leftovers.
        tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        return path

    def load(self, job_id: str) -> dict[str, Any] | None:
        """Return one validated job payload, or None when absent/unusable."""
        return self._decode(self._path(job_id))

    def load_all(self) -> list[dict[str, Any]]:
        """Return every valid job payload in the jobs directory.

        Tolerant by contract: corrupt or foreign files are logged once and
        skipped, never raised.
        """
        jobs: list[dict[str, Any]] = []
        for path in sorted(self._jobs_dir.glob("*.json")):
            job = self._decode(path)
            if job is not None:
                jobs.append(job)
        return jobs

    def delete(self, job_id: str) -> None:
        """Remove the snapshot file when present (explicit user deletion)."""
        self._path(job_id).unlink(missing_ok=True)

    def _path(self, job_id: str) -> Path:
        return self._jobs_dir / f"{job_id}.json"

    def _decode(self, path: Path) -> dict[str, Any] | None:
        """Parse and validate one snapshot file; None with a log line if bad."""
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError) as exc:
            logger.warning("skipping unusable run file %s: %s", path, type(exc).__name__)
            return None
        if not isinstance(payload, dict) or payload.get("schema") != SCHEMA_VERSION:
            logger.warning("skipping run file %s: unknown schema", path)
            return None
        job = payload.get("job")
        if not isinstance(job, dict) or not self._valid(job):
            logger.warning("skipping run file %s: invalid payload", path)
            return None
        return job

    @staticmethod
    def _valid(job: dict[str, Any]) -> bool:
        """Shape-check one job payload (defensive against truncated files)."""
        if not isinstance(job.get("id"), str) or not _JOB_ID_PATTERN.match(job["id"]):
            return False
        if job.get("status") not in _TERMINAL_STATUSES:
            return False
        for key in ("created_at", "terminal_at"):
            if not isinstance(job.get(key), (int, float)):
                return False
        if job.get("error") is not None and not isinstance(job.get("error"), str):
            return False
        spec = job.get("spec")
        if not isinstance(spec, dict):
            return False
        if not isinstance(spec.get("ticker"), str) or not isinstance(spec.get("date"), str):
            return False
        events = job.get("events")
        if not isinstance(events, list):
            return False
        return all(
            isinstance(envelope, dict)
            and isinstance(envelope.get("seq"), int)
            and isinstance(envelope.get("event"), dict)
            for envelope in events
        )
