# ADR 0008: Optional disk persistence of finished runs

Status: accepted

Context: runs and reports previously lived only in the ``JobManager``'s
in-memory registry. Finished jobs were evicted after a one-hour TTL sweep,
and every server restart discarded the entire history — users lost reports
when switching versions or restarting the service, with no recovery path.

Decision:

- **Opt-in by environment, on by launcher.** When ``TA_WEBGUI_PERSIST_DIR``
  is unset or empty, behavior is exactly the previous in-memory model
  (zero-risk default; also the test suite's baseline). The shipped ``run.sh``
  / ``run.bat`` default it to ``<repo>/data/runs`` (created on demand;
  ``data/`` is gitignored), so normal users get persistence without
  configuration. Pre-set the variable to override the location.
- **What is written.** Every job reaching a terminal state
  (``completed`` / ``failed`` / ``cancelled``) is written once to
  ``<dir>/jobs/<job_id>.json``: schema version, run spec metadata (ticker,
  date, asset type, provider, effective provider, instructions presence),
  terminal status, short error category, the full event-envelope log, and
  created/finished timestamps. Running or queued jobs are never persisted —
  they are ephemeral by design. A queued job cancelled before its worker
  starts is terminal, so it is persisted.
- **Format & atomicity.** One JSON document per job (schema ``1``), written
  to a unique hidden ``.tmp`` file, fsynced, then moved into place with
  ``os.replace`` — readers only ever see complete files, concurrent writers
  cannot collide on a shared temp name, and no ``.tmp`` leftovers remain.
  The per-job write happens under the job's lock during the terminal
  transition; failures are logged and swallowed (disk problems must never
  crash a worker or corrupt job state).
- **Recovery semantics.** At construction with a configured directory, the
  manager loads every ``*.json`` snapshot, validates shape and schema, and
  restores the jobs **read-only**: they are never re-executed, have no
  worker thread, and cannot be cancelled (already terminal). Status
  endpoints, the run list, SSE replay from cursor 0, and the Markdown
  report export (``GET /api/runs/{id}/report``) work for restored jobs.
  Corrupt, truncated, or foreign files are skipped with a single server-side
  warning line and never crash startup or lookups. Explicit deletion via
  ``DELETE /api/runs/{id}`` removes the in-memory entry *and* the disk file
  (a restored-then-deleted job stays deleted across restarts).
- **TTL interplay.** Persisted finished jobs are exempt from the 1-hour
  in-memory eviction sweep: with persistence active they stay in memory for
  the process lifetime, so lookups never pay a disk round-trip and the
  sweep's cheap per-call property is preserved. Evict-to-disk with lazy
  re-read on demand was considered and rejected: it adds a disk-failure path
  to every read, duplicates restore logic in each public entry point, and
  saves memory only for per-job histories that the ``max_events`` cap
  already bounds.
- **Privacy.** The engine's events are analysis content (agent stage
  status, report text, final decision) built from graph state — no API keys
  enter them by construction. Error hygiene is preserved: only short error
  categories are stored; full tracebacks stay in the server log. As defense
  in depth, a redaction pass scrubs any dict value under a secret-looking
  key (``api_key``, ``token``, ``password``, …) before anything reaches disk.

Alternatives considered: a SQLite database (rejected: a second stateful
store and migration surface for per-job documents that files model
naturally and users can inspect or back up by copying a directory);
evict-to-disk with lazy re-read (rejected above); persisting live runs for
crash-resume (rejected: resuming an LLM pipeline mid-stream is not
meaningful and would re-execute against fresh market data — restart
recovery is intentionally limited to finished history).

Consequences: finished runs survive restarts and version switches; the run
list and reports are stable across the 1-hour mark; disk grows with run
history (one JSON per run, event log capped by ``max_events``) and users
reclaim space with the existing delete action. Restored jobs keep the same
API contract, so the frontend needs no changes to display them.
