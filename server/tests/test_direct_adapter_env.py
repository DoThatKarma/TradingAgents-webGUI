"""Deployment-env overrides in the direct adapter (offline, ADR 0003).

The upstream ``tradingagents`` modules are stubbed via ``sys.modules`` so no
real graph, LLM client, or API key is ever touched: these tests pin the
``TA_WEBGUI_*`` contract only.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from app.instructions.adapters.direct import DirectProvider

_STUB_DEFAULT_CONFIG: dict[str, Any] = {
    "llm_provider": "openai",
    "quick_think_llm": "quick-default",
    "deep_think_llm": "deep-default",
    "max_debate_rounds": 1,
    "data_cache_dir": "/tmp/stub-cache",
    "results_dir": "/tmp/stub-results",
}


class StubUpstreamGraph:
    """Records constructor args instead of building LLM clients."""

    last_instance: StubUpstreamGraph | None = None

    def __init__(
        self,
        selected_analysts: tuple[str, ...] = ("market", "social", "news", "fundamentals"),
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self.selected_analysts = selected_analysts
        self.config = config
        StubUpstreamGraph.last_instance = self


def _install_upstream_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Register stub ``tradingagents`` modules; parents only if absent."""
    ta_pkg = sys.modules.get("tradingagents")
    if ta_pkg is None:
        ta_pkg = types.ModuleType("tradingagents")
    if not hasattr(ta_pkg, "__path__"):
        ta_pkg.__path__ = []  # type: ignore[attr-defined]  # mark as package
    graph_pkg = types.ModuleType("tradingagents.graph")
    graph_pkg.__path__ = []
    graph_mod = types.ModuleType("tradingagents.graph.trading_graph")
    graph_mod.TradingAgentsGraph = StubUpstreamGraph
    cfg_mod = types.ModuleType("tradingagents.default_config")
    cfg_mod.DEFAULT_CONFIG = dict(_STUB_DEFAULT_CONFIG)
    monkeypatch.setitem(sys.modules, "tradingagents", ta_pkg)
    monkeypatch.setitem(sys.modules, "tradingagents.graph", graph_pkg)
    monkeypatch.setitem(sys.modules, "tradingagents.graph.trading_graph", graph_mod)
    monkeypatch.setitem(sys.modules, "tradingagents.default_config", cfg_mod)


@pytest.fixture(autouse=True)
def _stubbed_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_upstream_stubs(monkeypatch)
    StubUpstreamGraph.last_instance = None


def _build_with_env(monkeypatch: pytest.MonkeyPatch, **env: str) -> StubUpstreamGraph:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    DirectProvider().build_runner("NVDA", "2026-09-11")
    graph = StubUpstreamGraph.last_instance
    assert graph is not None
    return graph


def test_no_env_vars_apply_gui_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "TA_WEBGUI_LLM_PROVIDER",
        "TA_WEBGUI_QUICK_MODEL",
        "TA_WEBGUI_DEEP_MODEL",
        "TA_WEBGUI_SELECTED_ANALYSTS",
        "TA_WEBGUI_MAX_DEBATE_ROUNDS",
        "TRADINGAGENTS_LLM_PROVIDER",
        "TRADINGAGENTS_QUICK_THINK_LLM",
        "TRADINGAGENTS_DEEP_THINK_LLM",
    ):
        monkeypatch.delenv(name, raising=False)
    DirectProvider().build_runner("NVDA", "2026-09-11")
    graph = StubUpstreamGraph.last_instance
    assert graph is not None
    assert graph.config["llm_provider"] == "openrouter"
    assert graph.config["quick_think_llm"] == "z-ai/glm-5.3-flash"
    assert graph.config["deep_think_llm"] == "z-ai/glm-5.3-flash"
    # Non-baseline upstream defaults pass through untouched.
    assert graph.config["data_cache_dir"] == "/tmp/stub-cache"
    assert graph.selected_analysts == ("market", "social", "news", "fundamentals")


def test_upstream_tradingagents_env_beats_gui_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An explicit upstream import-time choice must not be overridden.
    graph = _build_with_env(
        monkeypatch,
        TRADINGAGENTS_LLM_PROVIDER="openai",
        TRADINGAGENTS_QUICK_THINK_LLM="gpt-5.6-luna",
    )
    assert graph is not None
    assert graph.config["llm_provider"] == "openai"
    # Baseline skipped for quick role: the upstream env choice stands
    # (stub default here; the real upstream bakes it in at import time).
    assert graph.config["quick_think_llm"] == "quick-default"
    # Deep role keeps the GUI baseline: no upstream choice was made for it.
    assert graph.config["deep_think_llm"] == "z-ai/glm-5.3-flash"


def test_llm_env_overrides_applied_onto_copied_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _build_with_env(
        monkeypatch,
        TA_WEBGUI_LLM_PROVIDER="openrouter",
        TA_WEBGUI_QUICK_MODEL="vendor/flash-small",
        TA_WEBGUI_DEEP_MODEL="vendor/deep-cheap",
        TA_WEBGUI_MAX_DEBATE_ROUNDS="0",
    )
    assert graph is not None
    assert graph.config["llm_provider"] == "openrouter"
    assert graph.config["quick_think_llm"] == "vendor/flash-small"
    assert graph.config["deep_think_llm"] == "vendor/deep-cheap"
    assert graph.config["max_debate_rounds"] == 0
    # Unrelated upstream defaults pass through untouched.
    assert graph.config["data_cache_dir"] == "/tmp/stub-cache"


def test_default_config_is_never_mutated(monkeypatch: pytest.MonkeyPatch) -> None:
    _build_with_env(monkeypatch, TA_WEBGUI_LLM_PROVIDER="openrouter")
    assert StubUpstreamGraph.last_instance is not None
    assert sys.modules["tradingagents.default_config"].DEFAULT_CONFIG == _STUB_DEFAULT_CONFIG


def test_selected_analysts_env_parses_aliases_and_spaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _build_with_env(monkeypatch, TA_WEBGUI_SELECTED_ANALYSTS=" market_analyst , NEWS ")
    assert graph is not None
    assert graph.selected_analysts == ("market", "news")


def test_unknown_analyst_raises_value_error(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="unknown analyst"):
        _build_with_env(monkeypatch, TA_WEBGUI_SELECTED_ANALYSTS="market,chocolate")


@pytest.mark.parametrize("bad", ["abc", "-1", "1.5"])
def test_invalid_debate_rounds_raise(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    with pytest.raises(ValueError, match="TA_WEBGUI_MAX_DEBATE_ROUNDS"):
        _build_with_env(monkeypatch, TA_WEBGUI_MAX_DEBATE_ROUNDS=bad)


def test_blank_env_values_are_treated_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _build_with_env(monkeypatch, TA_WEBGUI_LLM_PROVIDER="   ")
    assert graph is not None
    # Blank = unset -> the shipped GUI baseline applies.
    assert graph.config["llm_provider"] == "openrouter"


def test_instructions_still_rejected_for_direct_provider() -> None:
    with pytest.raises(ValueError, match="ta_plugins"):
        DirectProvider().build_runner("NVDA", "2026-09-11", instructions="be bullish")
