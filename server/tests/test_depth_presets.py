"""Depth presets (ADR 0007): mapping, precedence, validation, adapter wiring.

Offline: upstream ``tradingagents`` modules are stubbed via ``sys.modules``
and ``ta_plugins`` via a minimal recording stub (same pattern as
``test_graph_config.py``), so no real graph, LLM client, or API key is ever
touched. Contracts pinned here:

- each preset maps exactly the keys from the ADR 0007 table;
- a depth preset beats ``TA_WEBGUI_*`` on its mapped keys, while env keeps
  winning on keys the preset does not map (and ``standard`` maps nothing);
- invalid depth is rejected at the resolver, the RunSpec boundary, the job
  manager, and the API (static 422 envelope);
- both adapters receive the depth-resolved config and analyst set.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import httpx
import pytest

from app.engine.runner import RunSpec
from app.instructions import graph_config
from app.instructions.adapters.direct import DirectProvider
from app.instructions.adapters.ta_plugins_adapter import TAPluginsProvider

_STUB_DEFAULT_CONFIG: dict[str, Any] = {
    "llm_provider": "openai",
    "quick_think_llm": "quick-default",
    "deep_think_llm": "deep-default",
    "max_debate_rounds": 1,
    "max_risk_discuss_rounds": 1,
    "max_recur_limit": 100,
    "news_article_limit": 20,
    "global_news_article_limit": 10,
    "global_news_lookback_days": 7,
    "openai_reasoning_effort": None,
    "google_thinking_level": None,
    "anthropic_effort": None,
    "data_cache_dir": "/tmp/stub-cache",
    "results_dir": "/tmp/stub-results",
}

_KNOWN_ENV: tuple[str, ...] = (
    "TA_WEBGUI_LLM_PROVIDER",
    "TA_WEBGUI_QUICK_MODEL",
    "TA_WEBGUI_DEEP_MODEL",
    "TA_WEBGUI_MAX_DEBATE_ROUNDS",
    "TA_WEBGUI_SELECTED_ANALYSTS",
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
    mod = types.ModuleType("ta_plugins")
    mod.FACTORY_NAMES = frozenset({"create_market_analyst", "create_msg_delete"})  # type: ignore[attr-defined]

    def prompt_prefix_plugin(instructions: str, targets: list[str]) -> dict[str, Any]:
        return {"instructions": instructions, "targets": list(targets)}

    import contextlib

    @contextlib.contextmanager
    def plugin_scope(plugins: list[Any]):
        yield

    mod.prompt_prefix_plugin = prompt_prefix_plugin  # type: ignore[attr-defined]
    mod.plugin_scope = plugin_scope  # type: ignore[attr-defined]
    return mod


def _install_upstream_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    ta_pkg = sys.modules.get("tradingagents")
    if ta_pkg is None:
        ta_pkg = types.ModuleType("tradingagents")
    if not hasattr(ta_pkg, "__path__"):
        ta_pkg.__path__ = []  # type: ignore[attr-defined]
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
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clean env + stubbed upstream and ta_plugins for every test here."""
    for name in _KNOWN_ENV:
        monkeypatch.delenv(name, raising=False)
    _install_upstream_stubs(monkeypatch)
    monkeypatch.setitem(sys.modules, "ta_plugins", _make_ta_plugins_stub())
    StubUpstreamGraph.last_instance = None


# --- preset mapping -----------------------------------------------------------


def test_fast_preset_maps_expected_keys() -> None:
    config = graph_config.resolve_graph_config("fast")
    assert config["max_debate_rounds"] == 0
    assert config["max_risk_discuss_rounds"] == 0
    # deep model collapses onto the resolved quick model.
    assert config["deep_think_llm"] == config["quick_think_llm"]
    assert config["news_article_limit"] == 10
    assert config["global_news_article_limit"] == 5
    assert config["global_news_lookback_days"] == 3
    # unmapped safety knob keeps the upstream default.
    assert config["max_recur_limit"] == 100
    assert graph_config.resolve_selected_analysts("fast") == ("market", "fundamentals")


def test_standard_preset_maps_nothing() -> None:
    config = graph_config.resolve_graph_config("standard")
    assert config["max_debate_rounds"] == 1
    assert config["max_risk_discuss_rounds"] == 1
    assert config["news_article_limit"] == 20
    assert config["global_news_article_limit"] == 10
    assert config["global_news_lookback_days"] == 7
    assert config["openai_reasoning_effort"] is None
    assert graph_config.resolve_selected_analysts("standard") == (
        "market",
        "social",
        "news",
        "fundamentals",
    )


def test_deep_preset_maps_expected_keys() -> None:
    config = graph_config.resolve_graph_config("deep")
    assert config["max_debate_rounds"] == 3
    assert config["max_risk_discuss_rounds"] == 2
    assert config["news_article_limit"] == 30
    assert config["global_news_article_limit"] == 15
    assert config["global_news_lookback_days"] == 14
    assert config["max_recur_limit"] == 150
    # deep keeps the default analyst set and the resolved deep model.
    assert graph_config.resolve_selected_analysts("deep") == (
        "market",
        "social",
        "news",
        "fundamentals",
    )
    assert config["deep_think_llm"] == "z-ai/glm-5.3-flash"


def test_deep_model_follows_quick_after_env_layering(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fast's deep-model sentinel reads the quick model *after* env resolution."""
    monkeypatch.setenv("TA_WEBGUI_QUICK_MODEL", "vendor/flash-x")
    config = graph_config.resolve_graph_config("fast")
    assert config["quick_think_llm"] == "vendor/flash-x"
    assert config["deep_think_llm"] == "vendor/flash-x"


# --- provider-aware effort ------------------------------------------------------


@pytest.mark.parametrize(
    ("provider", "key"),
    [
        ("openai", "openai_reasoning_effort"),
        ("google", "google_thinking_level"),
        ("anthropic", "anthropic_effort"),
    ],
)
def test_effort_applies_to_matching_provider_only(
    monkeypatch: pytest.MonkeyPatch, provider: str, key: str
) -> None:
    monkeypatch.setenv("TA_WEBGUI_LLM_PROVIDER", provider)
    deep = graph_config.resolve_graph_config("deep")
    assert deep[key] == "high"
    fast = graph_config.resolve_graph_config("fast")
    assert fast[key] == "low"


def test_effort_skips_unmapped_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TA_WEBGUI_LLM_PROVIDER", "openrouter")
    config = graph_config.resolve_graph_config("deep")
    assert config["openai_reasoning_effort"] is None
    assert config["google_thinking_level"] is None
    assert config["anthropic_effort"] is None


# --- precedence -----------------------------------------------------------------


def test_depth_beats_ta_webgui_on_mapped_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TA_WEBGUI_MAX_DEBATE_ROUNDS", "1")
    config = graph_config.resolve_graph_config("deep")
    assert config["max_debate_rounds"] == 3


def test_depth_preset_beats_env_analysts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TA_WEBGUI_SELECTED_ANALYSTS", "market,news")
    assert graph_config.resolve_selected_analysts("fast") == ("market", "fundamentals")


def test_env_still_wins_on_unmapped_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keys outside the preset keep the documented env precedence."""
    monkeypatch.setenv("TA_WEBGUI_QUICK_MODEL", "vendor/flash-x")
    config = graph_config.resolve_graph_config("deep")
    assert config["quick_think_llm"] == "vendor/flash-x"
    # deep does not remap deep_think_llm: the explicit env choice stands.
    monkeypatch.setenv("TA_WEBGUI_DEEP_MODEL", "vendor/deep-x")
    config = graph_config.resolve_graph_config("deep")
    assert config["deep_think_llm"] == "vendor/deep-x"


def test_standard_keeps_env_overrides_fully_effective(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default depth: pure-env deployments behave exactly as before (ADR 0007)."""
    monkeypatch.setenv("TA_WEBGUI_MAX_DEBATE_ROUNDS", "0")
    monkeypatch.setenv("TA_WEBGUI_SELECTED_ANALYSTS", "market")
    config = graph_config.resolve_graph_config("standard")
    assert config["max_debate_rounds"] == 0
    assert graph_config.resolve_selected_analysts("standard") == ("market",)


def test_depth_does_not_mutate_upstream_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    graph_config.resolve_graph_config("deep")
    assert sys.modules["tradingagents.default_config"].DEFAULT_CONFIG == _STUB_DEFAULT_CONFIG


# --- validation -------------------------------------------------------------------


def test_unknown_depth_rejected_at_resolver() -> None:
    with pytest.raises(ValueError, match="unknown depth"):
        graph_config.resolve_graph_config("turbo")
    with pytest.raises(ValueError, match="unknown depth"):
        graph_config.resolve_selected_analysts("turbo")


def test_none_depth_behaves_like_pre_depth() -> None:
    """``depth=None`` keeps the historical resolver signature for old callers."""
    config = graph_config.resolve_graph_config()
    assert config["max_debate_rounds"] == 1
    assert graph_config.resolve_selected_analysts() == (
        "market",
        "social",
        "news",
        "fundamentals",
    )


def test_manager_rejects_unknown_depth() -> None:
    from app.jobs.manager import JobManager

    with pytest.raises(ValueError, match="unknown depth"):
        JobManager().submit_run({"ticker": "NVDA", "date": "2025-01-10", "depth": "turbo"})


def test_runspec_defaults_to_standard() -> None:
    spec = RunSpec(ticker="NVDA", date="2025-01-10")
    assert spec.depth == "standard"


# --- adapters receive depth-resolved config (recording fake) ----------------------


def test_direct_adapter_receives_depth_resolved_config() -> None:
    DirectProvider().build_runner("NVDA", "2025-01-10", depth="fast")
    graph = StubUpstreamGraph.last_instance
    assert graph is not None
    assert graph.config["max_debate_rounds"] == 0
    assert graph.selected_analysts == ("market", "fundamentals")


def test_ta_plugins_plain_site_receives_depth_resolved_config() -> None:
    TAPluginsProvider().build_runner("NVDA", "2025-01-10", depth="deep")
    graph = StubUpstreamGraph.last_instance
    assert graph is not None
    assert graph.config["max_debate_rounds"] == 3
    assert graph.config["max_recur_limit"] == 150


def test_ta_plugins_plugin_site_receives_depth_resolved_config() -> None:
    TAPluginsProvider().build_runner("NVDA", "2025-01-10", instructions="be bullish", depth="fast")
    graph = StubUpstreamGraph.last_instance
    assert graph is not None
    assert graph.config["max_debate_rounds"] == 0
    assert graph.selected_analysts == ("market", "fundamentals")


def test_both_adapters_resolve_identical_depth_config() -> None:
    DirectProvider().build_runner("NVDA", "2025-01-10", depth="deep")
    direct_graph = StubUpstreamGraph.last_instance
    assert direct_graph is not None
    StubUpstreamGraph.last_instance = None
    TAPluginsProvider().build_runner("NVDA", "2025-01-10", depth="deep")
    plugins_graph = StubUpstreamGraph.last_instance
    assert plugins_graph is not None
    assert direct_graph.config == plugins_graph.config
    assert direct_graph.selected_analysts == plugins_graph.selected_analysts


def test_adapter_rejects_unknown_depth() -> None:
    with pytest.raises(ValueError, match="unknown depth"):
        DirectProvider().build_runner("NVDA", "2025-01-10", depth="turbo")


# --- API level ---------------------------------------------------------------------


def test_api_accepts_depth_and_echoes_it() -> None:
    from app.api.app import create_app
    from app.engine.runner import RunEngine
    from tests.stubs import StubGraphFactory

    app = create_app(engine=RunEngine(graph_factory=StubGraphFactory()))

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/api/runs", json={"ticker": "NVDA", "date": "2025-01-10", "depth": "fast"}
            )
            assert created.status_code == 202
            job_id = created.json()["job_id"]
            status = (await client.get(f"/api/runs/{job_id}")).json()
            assert status["depth"] == "fast"
            # Default request resolves to standard.
            created = await client.post("/api/runs", json={"ticker": "MSFT", "date": "2025-01-10"})
            status = (await client.get(f"/api/runs/{created.json()['job_id']}")).json()
            assert status["depth"] == "standard"

    import asyncio

    asyncio.run(scenario())


def test_api_rejects_unknown_depth_with_static_422() -> None:
    from app.api.app import create_app
    from app.engine.runner import RunEngine
    from tests.stubs import StubGraphFactory

    app = create_app(engine=RunEngine(graph_factory=StubGraphFactory()))

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/runs", json={"ticker": "NVDA", "date": "2025-01-10", "depth": "turbo"}
            )
            assert response.status_code == 422
            # Static envelope: no echo of the rejected value (M1).
            assert response.json() == {"detail": "invalid run request"}

    import asyncio

    asyncio.run(scenario())
