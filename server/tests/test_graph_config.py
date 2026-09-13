"""Shared graph-config resolution (graph_config) and both-adapter parity.

Offline: upstream ``tradingagents`` modules are stubbed via ``sys.modules``
and ``ta_plugins`` via a minimal recording stub, so no real graph, LLM
client, or API key is ever touched. Tests pin the resolution precedence
(GUI baseline < upstream ``TRADINGAGENTS_*`` < ``TA_WEBGUI_*``) and the
bug-fix contract that the ``ta_plugins`` adapter passes the resolved config
(at both of its call sites) instead of constructing with upstream defaults.
"""

from __future__ import annotations

import contextlib
import sys
import types
from collections.abc import Iterator
from typing import Any

import pytest

from app.instructions import graph_config
from app.instructions.adapters.direct import DirectProvider
from app.instructions.adapters.ta_plugins_adapter import TAPluginsProvider

_STUB_DEFAULT_CONFIG: dict[str, Any] = {
    "llm_provider": "openai",
    "quick_think_llm": "quick-default",
    "deep_think_llm": "deep-default",
    "max_debate_rounds": 1,
    "data_cache_dir": "/tmp/stub-cache",
    "results_dir": "/tmp/stub-results",
}

_KNOWN_ENV: tuple[str, ...] = (
    "TA_WEBGUI_LLM_PROVIDER",
    "TA_WEBGUI_QUICK_MODEL",
    "TA_WEBGUI_DEEP_MODEL",
    "TA_WEBGUI_MAX_DEBATE_ROUNDS",
    "TA_WEBGUI_SELECTED_ANALYSTS",
    "TRADINGAGENTS_LLM_PROVIDER",
    "TRADINGAGENTS_QUICK_THINK_LLM",
    "TRADINGAGENTS_DEEP_THINK_LLM",
)


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


def _make_ta_plugins_stub() -> types.ModuleType:
    """Minimal ``ta_plugins`` stand-in: plugin factory + recording scope."""
    mod = types.ModuleType("ta_plugins")
    mod.FACTORY_NAMES = frozenset({"create_market_analyst", "create_msg_delete"})  # type: ignore[attr-defined]
    mod.last_scopes: list[list[Any]] = []  # type: ignore[attr-defined]

    def prompt_prefix_plugin(instructions: str, targets: list[str]) -> dict[str, Any]:
        return {"instructions": instructions, "targets": list(targets)}

    @contextlib.contextmanager
    def plugin_scope(plugins: list[Any]) -> Iterator[None]:
        mod.last_scopes.append(list(plugins))  # type: ignore[attr-defined]
        yield

    mod.prompt_prefix_plugin = prompt_prefix_plugin  # type: ignore[attr-defined]
    mod.plugin_scope = plugin_scope  # type: ignore[attr-defined]
    return mod


def _install_upstream_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub ``tradingagents`` modules; parents only if absent (shared pattern)."""
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
def _offline(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Clean env + stubbed upstream and ta_plugins for every test here."""
    for name in _KNOWN_ENV:
        monkeypatch.delenv(name, raising=False)
    _install_upstream_stubs(monkeypatch)
    monkeypatch.setitem(sys.modules, "ta_plugins", _make_ta_plugins_stub())
    StubUpstreamGraph.last_instance = None


def _baseline_config() -> dict[str, Any]:
    config = dict(_STUB_DEFAULT_CONFIG)
    config["llm_provider"] = "openrouter"
    config["quick_think_llm"] = "z-ai/glm-5.3-flash"
    config["deep_think_llm"] = "z-ai/glm-5.3-flash"
    return config


# --- resolution precedence (resolver directly) -------------------------------


def test_clean_env_baseline_openrouter_glm() -> None:
    config = graph_config.resolve_graph_config()
    assert config["llm_provider"] == "openrouter"
    assert config["quick_think_llm"] == "z-ai/glm-5.3-flash"
    assert config["deep_think_llm"] == "z-ai/glm-5.3-flash"
    # Non-baseline upstream defaults pass through untouched.
    assert config["data_cache_dir"] == "/tmp/stub-cache"


def test_upstream_tradingagents_env_beats_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRADINGAGENTS_LLM_PROVIDER", "openai")
    config = graph_config.resolve_graph_config()
    assert config["llm_provider"] == "openai"
    # Per-key: upstream choice only suppresses the baseline for its own key.
    assert config["quick_think_llm"] == "z-ai/glm-5.3-flash"


def test_ta_webgui_beats_tradingagents_per_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRADINGAGENTS_LLM_PROVIDER", "openai")
    monkeypatch.setenv("TA_WEBGUI_LLM_PROVIDER", "openrouter")
    config = graph_config.resolve_graph_config()
    assert config["llm_provider"] == "openrouter"


@pytest.mark.parametrize("bad", ["abc", "-1", "1.5"])
def test_invalid_max_debate_rounds_raises(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    monkeypatch.setenv("TA_WEBGUI_MAX_DEBATE_ROUNDS", bad)
    with pytest.raises(ValueError, match="TA_WEBGUI_MAX_DEBATE_ROUNDS"):
        graph_config.resolve_graph_config()


@pytest.mark.parametrize("bad", ["market,chocolate", ",,,"])
def test_unknown_analyst_raises(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    monkeypatch.setenv("TA_WEBGUI_SELECTED_ANALYSTS", bad)
    with pytest.raises(ValueError, match="unknown analyst"):
        graph_config.resolve_selected_analysts()


def test_blank_analysts_env_is_treated_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TA_WEBGUI_SELECTED_ANALYSTS", "   ")
    assert graph_config.resolve_selected_analysts() == (
        "market",
        "social",
        "news",
        "fundamentals",
    )


# --- ta_plugins adapter passes resolved config at both call sites ------------


def test_ta_plugins_fallback_site_gets_baseline_config() -> None:
    TAPluginsProvider().build_runner("NVDA", "2026-09-11")
    graph = StubUpstreamGraph.last_instance
    assert graph is not None
    assert graph.config["llm_provider"] == "openrouter"
    assert graph.config["quick_think_llm"] == "z-ai/glm-5.3-flash"
    assert graph.selected_analysts == ("market", "social", "news", "fundamentals")


def test_ta_plugins_plugins_site_gets_overridden_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TA_WEBGUI_LLM_PROVIDER", "openai")
    TAPluginsProvider().build_runner("NVDA", "2026-09-11", instructions="be bullish")
    graph = StubUpstreamGraph.last_instance
    assert graph is not None
    assert graph.config["llm_provider"] == "openai"


def test_ta_plugins_plugins_site_applies_plugin_scope() -> None:
    TAPluginsProvider().build_runner("NVDA", "2026-09-11", instructions="be bullish")
    stub = sys.modules["ta_plugins"]
    assert stub.last_scopes, "plugin_scope must run for custom instructions"  # type: ignore[attr-defined]


# --- adapter parity ----------------------------------------------------------


def test_both_adapters_resolve_identical_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TA_WEBGUI_QUICK_MODEL", "vendor/flash-x")
    DirectProvider().build_runner("NVDA", "2026-09-11")
    direct_graph = StubUpstreamGraph.last_instance
    assert direct_graph is not None
    StubUpstreamGraph.last_instance = None
    TAPluginsProvider().build_runner("NVDA", "2026-09-11")
    plugins_graph = StubUpstreamGraph.last_instance
    assert plugins_graph is not None
    assert direct_graph.config == plugins_graph.config
    assert direct_graph.selected_analysts == plugins_graph.selected_analysts
    assert direct_graph.config["quick_think_llm"] == "vendor/flash-x"


def test_both_adapters_use_the_shared_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adapters must call graph_config's functions, not private copies."""
    from app.instructions.adapters import direct as direct_mod
    from app.instructions.adapters import ta_plugins_adapter as plugins_mod

    assert direct_mod.resolve_graph_config is graph_config.resolve_graph_config
    assert direct_mod.resolve_selected_analysts is graph_config.resolve_selected_analysts
    assert plugins_mod.resolve_graph_config is graph_config.resolve_graph_config
    assert plugins_mod.resolve_selected_analysts is graph_config.resolve_selected_analysts

    sentinel = _baseline_config()
    sentinel["llm_provider"] = "sentinel-provider"
    monkeypatch.setattr(direct_mod, "resolve_graph_config", lambda depth=None: sentinel)
    monkeypatch.setattr(direct_mod, "resolve_selected_analysts", lambda depth=None: ("market",))
    monkeypatch.setattr(plugins_mod, "resolve_graph_config", lambda depth=None: sentinel)
    monkeypatch.setattr(plugins_mod, "resolve_selected_analysts", lambda depth=None: ("market",))

    DirectProvider().build_runner("NVDA", "2026-09-11")
    direct_graph = StubUpstreamGraph.last_instance
    assert direct_graph is not None
    assert direct_graph.config["llm_provider"] == "sentinel-provider"
    assert direct_graph.selected_analysts == ("market",)

    StubUpstreamGraph.last_instance = None
    TAPluginsProvider().build_runner("NVDA", "2026-09-11")
    plugins_graph = StubUpstreamGraph.last_instance
    assert plugins_graph is not None
    assert plugins_graph.config["llm_provider"] == "sentinel-provider"
    assert plugins_graph.selected_analysts == ("market",)
