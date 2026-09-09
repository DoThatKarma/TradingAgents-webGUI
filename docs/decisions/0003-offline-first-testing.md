# ADR 0003: Offline-first testing

Status: accepted

No test may call a real LLM or the network. The engine accepts an injectable
graph factory; tests use stub graphs with canned state snapshots. Live smoke
tests are opt-in via marker and skipped without credentials. Rationale: CI
must be free, deterministic, and secret-free.
