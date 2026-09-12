"""Optional instruction provider backed by the ``ta_plugins`` plugin layer.

Implements ADR 0002: custom instructions are a plugin-framework feature. The
import is guarded, so a missing or import-broken ``ta_plugins`` raises a
clear :class:`ProviderUnavailableError` instead of a raw traceback.

Graph construction always receives the config resolved by
:mod:`app.instructions.graph_config` — identical to the direct adapter — so a
custom-instruction run uses the same LLM stack (GUI baseline, upstream
``TRADINGAGENTS_*`` layering, ``TA_WEBGUI_*`` overrides) as a plain run.

Construction happens inside ``ta_plugins.plugin_scope`` so patched factories
apply to exactly this run's graph and are restored afterwards. NOTE: plugin
application is process-global while the scope is active; callers must
serialize ``build_runner`` (the job manager holds a provider lock around
construction). This module is the only place besides the factory that may
import ``ta_plugins``.
"""

from __future__ import annotations

from app.instructions._upstream_runner import UpstreamRunner
from app.instructions.graph_config import resolve_graph_config, resolve_selected_analysts
from app.instructions.interface import GraphRunner, ProviderUnavailableError


class TAPluginsProvider:
    """Builds GraphRunners with per-agent prompt-prefix instructions."""

    name = "ta_plugins"

    def build_runner(
        self,
        ticker: str,
        date: str,
        asset_type: str = "stock",
        instructions: str | None = None,
    ) -> GraphRunner:
        if not instructions:
            # Plain run: identical to the direct provider (shared resolution).
            from tradingagents.graph.trading_graph import TradingAgentsGraph

            return UpstreamRunner(
                TradingAgentsGraph(
                    selected_analysts=resolve_selected_analysts(),
                    config=resolve_graph_config(),
                ),
                ticker,
                date,
                asset_type,
            )

        try:
            import ta_plugins
        except Exception as exc:  # missing or import-broken plugin layer
            msg = (
                "provider 'ta_plugins' is unavailable; install ta-plugins or use provider 'direct'"
            )
            raise ProviderUnavailableError(msg) from exc

        from tradingagents.graph.trading_graph import TradingAgentsGraph

        config = resolve_graph_config()
        analysts = resolve_selected_analysts()
        targets = sorted(set(ta_plugins.FACTORY_NAMES) - {"create_msg_delete"})
        plugin = ta_plugins.prompt_prefix_plugin(instructions, targets)
        with ta_plugins.plugin_scope([plugin]):
            graph = TradingAgentsGraph(selected_analysts=analysts, config=config)
        return UpstreamRunner(graph, ticker, date, asset_type)
