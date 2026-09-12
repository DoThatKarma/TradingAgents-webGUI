"""App-level protection: optional bearer gate + security headers (ADR 0005).

Covers token set/unset behaviour, static 401 hygiene, the ``/api/health``
exemption, header presence on every response (including 401s and SSE), and
that the token never leaks into response bodies. All scenarios run offline
against the injectable factory (ADR 0003).
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from app.api.app import create_app
from app.engine.runner import RunEngine
from tests.stubs import StubGraphFactory

_TOKEN_ENV = "TA_WEBGUI_API_TOKEN"
_TOKEN = "test-bearer-token-9f2c"
_WRONG_TOKEN = "wrong-token-value"
_RUN_BODY: dict[str, Any] = {"ticker": "NVDA", "date": "2025-01-10"}

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _app() -> Any:
    return create_app(engine=RunEngine(graph_factory=StubGraphFactory()))


def _assert_security_headers(response: httpx.Response) -> None:
    for name, value in _SECURITY_HEADERS.items():
        assert response.headers.get(name) == value, name


def test_health_open_without_token() -> None:
    async def scenario() -> None:
        async with _client(_app()) as client:
            response = await client.get("/api/health")
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}

    asyncio.run(scenario())


def test_token_unset_endpoints_behave_as_before() -> None:
    async def scenario() -> None:
        async with _client(_app()) as client:
            created = await client.post("/api/runs", json=_RUN_BODY)
            assert created.status_code == 202
            assert created.json()["status"] == "queued"
            assert (await client.get("/api/runs")).status_code == 200
            # Validation behaviour unchanged: static 422, no reflection.
            invalid = await client.post("/api/runs", json={"ticker": "BAD TICKER!"})
            assert invalid.status_code == 422
            assert invalid.json() == {"detail": "invalid run request"}
            assert _RUN_BODY["ticker"] not in invalid.text

    asyncio.run(scenario())


def test_token_set_missing_header_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_TOKEN_ENV, _TOKEN)

    async def scenario() -> None:
        async with _client(_app()) as client:
            response = await client.post("/api/runs", json=_RUN_BODY)
            assert response.status_code == 401
            assert response.json() == {"detail": "unauthorized"}
            assert response.headers["WWW-Authenticate"] == "Bearer"
            # No reflected input, no credential echo.
            assert _RUN_BODY["ticker"] not in response.text

    asyncio.run(scenario())


def test_bearer_scheme_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    # RFC 9110 11: auth schemes match case-insensitively (S3).
    monkeypatch.setenv(_TOKEN_ENV, _TOKEN)

    async def scenario() -> None:
        async with _client(_app()) as client:
            ok = await client.post(
                "/api/runs", json=_RUN_BODY, headers={"Authorization": f"bearer {_TOKEN}"}
            )
            assert ok.status_code == 202

    asyncio.run(scenario())


def test_docs_and_schema_gated_when_token_set(monkeypatch: pytest.MonkeyPatch) -> None:
    # /docs, /redoc, /openapi.json must not leak the API schema publicly (S4).
    monkeypatch.setenv(_TOKEN_ENV, _TOKEN)

    async def scenario() -> None:
        async with _client(_app()) as client:
            for path in ("/docs", "/redoc", "/openapi.json"):
                response = await client.get(path)
                assert response.status_code == 404, path

    asyncio.run(scenario())


def test_docs_available_without_token() -> None:
    async def scenario() -> None:
        async with _client(_app()) as client:
            response = await client.get("/openapi.json")
            assert response.status_code == 200

    asyncio.run(scenario())


def test_bare_api_prefix_is_protected(monkeypatch: pytest.MonkeyPatch) -> None:
    # GET /api (no trailing slash) must not bypass the gate.
    monkeypatch.setenv(_TOKEN_ENV, _TOKEN)

    async def scenario() -> None:
        async with _client(_app()) as client:
            response = await client.get("/api")
            assert response.status_code == 401

    asyncio.run(scenario())


def test_token_set_wrong_token_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_TOKEN_ENV, _TOKEN)

    async def scenario() -> None:
        headers = {"Authorization": f"Bearer {_WRONG_TOKEN}"}
        async with _client(_app()) as client:
            response = await client.post("/api/runs", json=_RUN_BODY, headers=headers)
            assert response.status_code == 401
            assert response.json() == {"detail": "unauthorized"}
            assert _WRONG_TOKEN not in response.text

    asyncio.run(scenario())


def test_token_set_non_bearer_scheme_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_TOKEN_ENV, _TOKEN)

    async def scenario() -> None:
        async with _client(_app()) as client:
            response = await client.post(
                "/api/runs", json=_RUN_BODY, headers={"Authorization": "Basic c3RpY2s6c3RpY2s="}
            )
            assert response.status_code == 401

    asyncio.run(scenario())


def test_token_set_correct_token_202(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_TOKEN_ENV, _TOKEN)

    async def scenario() -> None:
        headers = {"Authorization": f"Bearer {_TOKEN}"}
        async with _client(_app()) as client:
            created = await client.post("/api/runs", json=_RUN_BODY, headers=headers)
            assert created.status_code == 202
            assert created.json()["status"] == "queued"
            status = (
                await client.get(f"/api/runs/{created.json()['job_id']}", headers=headers)
            ).json()
            assert status["ticker"] == "NVDA"

    asyncio.run(scenario())


def test_health_stays_open_with_token_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_TOKEN_ENV, _TOKEN)

    async def scenario() -> None:
        async with _client(_app()) as client:
            response = await client.get("/api/health")
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}

    asyncio.run(scenario())


def test_get_routes_protected_with_token_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_TOKEN_ENV, _TOKEN)

    async def scenario() -> None:
        async with _client(_app()) as client:
            assert (await client.get("/api/runs")).status_code == 401
            assert (await client.get("/api/runs/unknown-id")).status_code == 401

    asyncio.run(scenario())


def test_whitespace_only_token_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_TOKEN_ENV, "   ")

    async def scenario() -> None:
        async with _client(_app()) as client:
            assert (await client.post("/api/runs", json=_RUN_BODY)).status_code == 202

    asyncio.run(scenario())


def test_security_headers_on_all_responses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_TOKEN_ENV, _TOKEN)

    async def scenario() -> None:
        async with _client(_app()) as client:
            # 200 (exempt route), 404, 422, and 401 all carry the headers.
            _assert_security_headers(await client.get("/api/health"))
            _assert_security_headers(await client.get("/api/runs/unknown-id"))
            _assert_security_headers(await client.post("/api/runs", json={"ticker": "BAD TICKER!"}))
            _assert_security_headers(await client.get("/api/runs"))

    asyncio.run(scenario())


def test_security_headers_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_TOKEN_ENV, raising=False)

    async def scenario() -> None:
        async with _client(_app()) as client:
            _assert_security_headers(await client.get("/api/health"))
            _assert_security_headers(await client.post("/api/runs", json=_RUN_BODY))

    asyncio.run(scenario())


def test_token_never_in_response_bodies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_TOKEN_ENV, _TOKEN)

    async def scenario() -> None:
        headers = {"Authorization": f"Bearer {_WRONG_TOKEN}"}
        async with _client(_app()) as client:
            unauthorized = await client.post("/api/runs", json=_RUN_BODY, headers=headers)
            assert _TOKEN not in unauthorized.text
            assert _WRONG_TOKEN not in unauthorized.text
            not_found = await client.get("/api/runs/unknown-id", headers=headers)
            assert _TOKEN not in not_found.text
            invalid = await client.post(
                "/api/runs", json={"ticker": "BAD TICKER!"}, headers=headers
            )
            assert _TOKEN not in invalid.text

    asyncio.run(scenario())


def test_sse_stream_with_token_and_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_TOKEN_ENV, _TOKEN)
    headers = {"Authorization": f"Bearer {_TOKEN}"}

    async def scenario() -> None:
        async with _client(_app()) as client:
            created = await client.post("/api/runs", json=_RUN_BODY, headers=headers)
            assert created.status_code == 202
            job_id = created.json()["job_id"]

            # The gate must not break the SSE route (middleware order safe).
            async with client.stream(
                "GET", f"/api/runs/{job_id}/events", headers=headers
            ) as stream:
                assert stream.status_code == 200
                assert "text/event-stream" in stream.headers["content-type"]
                _assert_security_headers(stream)
                body = (await stream.aread()).decode()
            assert "event: agent_status" in body
            assert "event: decision" in body

            # Without the credential the SSE route is guarded too.
            async with client.stream("GET", f"/api/runs/{job_id}/events") as stream:
                assert stream.status_code == 401
                _assert_security_headers(stream)

    asyncio.run(scenario())
