"""Default instruction provider: vanilla upstream graph, no custom instructions.

This adapter is the decoupling anchor of ADR 0002: it works with zero optional
dependencies. The upstream ``tradingagents`` package is imported lazily inside
:meth:`DirectProvider.build_runner` so importing this module never builds LLM
clients or requires API keys (ADR 0003). This module never imports
``ta_plugins``.

Environment-to-config resolution (GUI baseline, upstream ``TRADINGAGENTS_*``
layering, ``TA_WEBGUI_*`` deployment overrides) lives in
:mod:`app.instructions.graph_config` and is shared verbatim with the
``ta_plugins`` adapter — custom-instruction runs get exactly the same LLM
stack as plain runs.
"""

from __future__ import annotations

from app.instructions._upstream_runner import UpstreamRunner
from app.instructions.graph_config import resolve_graph_config, resolve_selected_analysts
from app.instructions.interface import GraphRunner


class DirectProvider:
    """Builds a GraphRunner around a plain ``TradingAgentsGraph``."""

    name = "direct"

    def build_runner(
        self,
        ticker: str,
        date: str,
        asset_type: str = "stock",
        instructions: str | None = None,
    ) -> GraphRunner:
        if instructions:
            raise ValueError(
                "the 'direct' provider does not support custom instructions; "
                "use provider 'ta_plugins' instead"
            )
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        graph = TradingAgentsGraph(
            selected_analysts=resolve_selected_analysts(), config=resolve_graph_config()
        )
        return UpstreamRunner(graph, ticker, date, asset_type)
