"""Default instruction provider: vanilla upstream graph, no custom instructions.

This adapter is the decoupling anchor of ADR 0002: it works with zero optional
dependencies. The upstream ``tradingagents`` package is imported lazily inside
:meth:`DirectProvider.build_runner` so importing this module never builds LLM
clients or requires API keys (ADR 0003). This module never imports
``ta_plugins``.
"""

from __future__ import annotations

from app.instructions._upstream_runner import UpstreamRunner
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

        graph = TradingAgentsGraph()
        return UpstreamRunner(graph, ticker, date, asset_type)
