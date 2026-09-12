"""Single-process SPA hosting (ADR 0006): opt-in static dist serving.

Covers explicit ``TA_WEBGUI_STATIC_DIR`` configuration, bundled-dir
auto-detection (candidates patched, fully hermetic), explicit-beats-auto
precedence, the SPA fallback for client-side routes, untouched ``/api/*``
semantics, fail-open behaviour on invalid/missing targets (with warnings),
and security headers on static responses. All offline (ADR 0003).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.api import app as app_module
from app.api.app import create_app
from app.engine.runner import RunEngine
from tests.stubs import StubGraphFactory

_STATIC_ENV = "TA_WEBGUI_STATIC_DIR"
_INDEX = "<!doctype html><title>webgui</title><div id=root></div>"
_INDEX_EXPLICIT = "<!doctype html><title>explicit</title>"
_INDEX_BUNDLED = "<!doctype html><title>bundled</title>"
_ASSET_JS = "console.log('asset');\n"
_RUN_BODY: dict[str, Any] = {"ticker": "NVDA", "date": "2025-01-10"}


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _app() -> Any:
    return create_app(engine=RunEngine(graph_factory=StubGraphFactory()))


def _make_dist(tmp_path: Path, *, name: str, index: str = _INDEX) -> Path:
    """Build a minimal fake SPA dist: index.html + assets/app.js."""
    dist = tmp_path / name
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(index, encoding="utf-8")
    (dist / "assets" / "app.js").write_text(_ASSET_JS, encoding="utf-8")
    return dist


def _no_bundled(monkeypatch: pytest.MonkeyPatch) -> None:
    # Hermetic auto-detect: no candidates outside the test's tmp dirs.
    monkeypatch.setattr(app_module, "_bundled_static_candidates", list)


def test_malformed_and_null_byte_paths_return_404_never_500(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Security-review regression: bad paths fail closed as 404 (no 500s)."""
    monkeypatch.setenv(_STATIC_ENV, str(_make_dist(tmp_path, name="dist")))
    _no_bundled(monkeypatch)

    async def scenario() -> None:
        async with _client(_app()) as client:
            for path in ("/foo%00bar", "/..%00/secret.txt", "/%2e%2e/%00", "/..%2fsecret"):
                response = await client.get(path)
                assert response.status_code == 404, (path, response.status_code)

    asyncio.run(scenario())


def test_disabled_mode_keeps_current_404(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(_STATIC_ENV, raising=False)
    _no_bundled(monkeypatch)

    async def scenario() -> None:
        async with _client(_app()) as client:
            assert (await client.get("/")).status_code == 404
            assert (await client.get("/some/route")).status_code == 404
            assert (await client.get("/api/health")).status_code == 200

    asyncio.run(scenario())


def test_enabled_serves_index_at_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(_STATIC_ENV, str(_make_dist(tmp_path, name="dist")))
    _no_bundled(monkeypatch)

    async def scenario() -> None:
        async with _client(_app()) as client:
            response = await client.get("/")
            assert response.status_code == 200
            assert _INDEX in response.text
            assert response.headers["content-type"].startswith("text/html")

    asyncio.run(scenario())


def test_enabled_serves_assets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(_STATIC_ENV, str(_make_dist(tmp_path, name="dist")))
    _no_bundled(monkeypatch)

    async def scenario() -> None:
        async with _client(_app()) as client:
            response = await client.get("/assets/app.js")
            assert response.status_code == 200
            assert _ASSET_JS in response.text

    asyncio.run(scenario())


def test_spa_fallback_serves_index_for_unknown_non_api_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(_STATIC_ENV, str(_make_dist(tmp_path, name="dist")))
    _no_bundled(monkeypatch)

    async def scenario() -> None:
        async with _client(_app()) as client:
            response = await client.get("/some/route")
            assert response.status_code == 200
            assert _INDEX in response.text
            assert response.headers["content-type"].startswith("text/html")

    asyncio.run(scenario())


def test_unknown_api_route_keeps_404(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(_STATIC_ENV, str(_make_dist(tmp_path, name="dist")))
    _no_bundled(monkeypatch)

    async def scenario() -> None:
        async with _client(_app()) as client:
            response = await client.get("/api/nope")
            assert response.status_code == 404
            # Health check unaffected by static hosting.
            assert (await client.get("/api/health")).status_code == 200

    asyncio.run(scenario())


def test_api_runs_still_work_when_enabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(_STATIC_ENV, str(_make_dist(tmp_path, name="dist")))
    _no_bundled(monkeypatch)

    async def scenario() -> None:
        async with _client(_app()) as client:
            created = await client.post("/api/runs", json=_RUN_BODY)
            assert created.status_code == 202
            assert created.json()["status"] == "queued"

    asyncio.run(scenario())


def test_auto_detect_serves_bundled_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(_STATIC_ENV, raising=False)
    bundled = _make_dist(tmp_path, name="bundled", index=_INDEX_BUNDLED)
    monkeypatch.setattr(app_module, "_bundled_static_candidates", lambda: [bundled])

    async def scenario() -> None:
        async with _client(_app()) as client:
            response = await client.get("/")
            assert response.status_code == 200
            assert _INDEX_BUNDLED in response.text

    asyncio.run(scenario())


def test_no_auto_detect_when_no_candidates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(_STATIC_ENV, raising=False)
    _no_bundled(monkeypatch)

    async def scenario() -> None:
        async with _client(_app()) as client:
            assert (await client.get("/")).status_code == 404

    asyncio.run(scenario())


def test_explicit_env_beats_auto_detect(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    explicit = _make_dist(tmp_path, name="explicit", index=_INDEX_EXPLICIT)
    bundled = _make_dist(tmp_path, name="bundled", index=_INDEX_BUNDLED)
    monkeypatch.setenv(_STATIC_ENV, str(explicit))
    monkeypatch.setattr(app_module, "_bundled_static_candidates", lambda: [bundled])

    async def scenario() -> None:
        async with _client(_app()) as client:
            response = await client.get("/")
            assert response.status_code == 200
            assert _INDEX_EXPLICIT in response.text
            assert _INDEX_BUNDLED not in response.text

    asyncio.run(scenario())


def test_nonexistent_configured_dir_warns_and_fails_open(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    missing = tmp_path / "does-not-exist"
    monkeypatch.setenv(_STATIC_ENV, str(missing))
    _no_bundled(monkeypatch)

    async def scenario() -> None:
        async with _client(_app()) as client:
            # Factory must not crash; app stays API-only.
            assert (await client.get("/")).status_code == 404
            assert (await client.get("/api/health")).status_code == 200

    with caplog.at_level(logging.WARNING, logger="app.api.app"):
        asyncio.run(scenario())
    assert any(_STATIC_ENV in record.getMessage() for record in caplog.records)


def test_configured_dir_without_index_warns_and_fails_open(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    empty = tmp_path / "empty-dir"
    empty.mkdir()
    monkeypatch.setenv(_STATIC_ENV, str(empty))
    _no_bundled(monkeypatch)

    async def scenario() -> None:
        async with _client(_app()) as client:
            assert (await client.get("/")).status_code == 404

    with caplog.at_level(logging.WARNING, logger="app.api.app"):
        asyncio.run(scenario())
    assert any("index.html" in record.getMessage() for record in caplog.records)


def test_security_headers_on_static_responses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(_STATIC_ENV, str(_make_dist(tmp_path, name="dist")))
    _no_bundled(monkeypatch)

    async def scenario() -> None:
        async with _client(_app()) as client:
            for path in ("/", "/assets/app.js", "/some/route"):
                response = await client.get(path)
                assert response.headers["X-Content-Type-Options"] == "nosniff", path
                assert response.headers["X-Frame-Options"] == "DENY", path
                assert response.headers["Referrer-Policy"] == "no-referrer", path
                assert response.headers["Cache-Control"] == "no-store", path

    asyncio.run(scenario())
