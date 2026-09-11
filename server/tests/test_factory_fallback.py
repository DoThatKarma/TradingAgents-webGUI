"""Provider registry fallback and backend-core import hygiene (ADR 0002)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.instructions.adapters.direct import DirectProvider
from app.instructions.factory import get_provider

SERVER_DIR = Path(__file__).resolve().parents[1]

GUARD_SCRIPT = """
import sys
import app.engine.runner
import app.jobs.manager
import app.api.app
for leaked in ('ta_plugins', 'tradingagents'):
    assert leaked not in sys.modules, leaked + ' leaked into backend core'
print('guard-ok')
"""


def test_unknown_provider_raises() -> None:
    with pytest.raises(ValueError, match="unknown provider"):
        get_provider("does-not-exist")


def test_direct_is_default_provider() -> None:
    assert isinstance(get_provider("direct"), DirectProvider)
    assert isinstance(get_provider(), DirectProvider)


def test_ta_plugins_broken_falls_back_to_direct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A None entry in sys.modules makes `import ta_plugins` raise ImportError.
    monkeypatch.setitem(sys.modules, "ta_plugins", None)
    provider = get_provider("ta_plugins")
    assert isinstance(provider, DirectProvider)


def test_backend_core_never_imports_ta_plugins_or_upstream() -> None:
    env = {**os.environ, "PYTHONPATH": str(SERVER_DIR)}
    proc = subprocess.run(
        [sys.executable, "-c", GUARD_SCRIPT],
        cwd=SERVER_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "guard-ok" in proc.stdout
