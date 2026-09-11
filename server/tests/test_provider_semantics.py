"""Provider fallback semantics and per-job effective_provider (ADR 0002, fix S1)."""

from __future__ import annotations

import logging
import sys
from typing import Any

import pytest

from app.engine.runner import RunEngine
from app.instructions.adapters.direct import DirectProvider
from app.instructions.factory import get_provider
from app.instructions.interface import ProviderUnavailableError
from app.jobs.manager import JobManager
from tests.stubs import StubRunner, standard_snapshots


def test_broken_ta_plugins_with_instructions_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "ta_plugins", None)
    with pytest.raises(ProviderUnavailableError, match="ta_plugins"):
        get_provider("ta_plugins", instructions="Focus on semiconductor margins")


def test_broken_ta_plugins_without_instructions_falls_back(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setitem(sys.modules, "ta_plugins", None)
    with caplog.at_level(logging.WARNING, logger="app.instructions.factory"):
        provider = get_provider("ta_plugins")
    assert isinstance(provider, DirectProvider)
    assert provider.name == "direct"
    # ADR 0002: the fallback must be observable in the server log.
    assert any("falling back" in record.message for record in caplog.records)


def _stub_build_runner(*args: Any, **kwargs: Any) -> StubRunner:
    # Offline stand-in for the upstream import in DirectProvider.build_runner.
    return StubRunner(standard_snapshots(), signal="Buy")


def test_job_records_effective_provider_after_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "ta_plugins", None)
    monkeypatch.setattr(DirectProvider, "build_runner", _stub_build_runner)
    manager = JobManager(engine=RunEngine())  # real default factory + real resolution

    job_id = manager.submit_run({"ticker": "NVDA", "date": "2025-01-10", "provider": "ta_plugins"})
    final = manager.wait(job_id)

    assert final["status"] == "completed"
    assert final["provider"] == "ta_plugins"
    assert final["effective_provider"] == "direct"


def test_job_records_effective_provider_direct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(DirectProvider, "build_runner", _stub_build_runner)
    manager = JobManager(engine=RunEngine())

    job_id = manager.submit_run({"ticker": "NVDA", "date": "2025-01-10"})
    final = manager.wait(job_id)

    assert final["status"] == "completed"
    assert final["provider"] == "direct"
    assert final["effective_provider"] == "direct"
