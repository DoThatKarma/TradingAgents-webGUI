"""Default instruction provider: vanilla upstream graph, no custom instructions.

This adapter is the decoupling anchor of ADR 0002: it works with zero optional
dependencies. The upstream ``tradingagents`` package is imported lazily inside
:meth:`DirectProvider.build_runner` so importing this module never builds LLM
clients or requires API keys (ADR 0003). This module never imports
``ta_plugins``.

Deployment-env overrides: a handful of ``TA_WEBGUI_*`` variables (documented in
the root README) are applied onto a *copied* ``DEFAULT_CONFIG`` per runner
build. They exist because ``selected_analysts`` has no upstream env override at
all, and so a deployment can pin the LLM stack without depending on upstream
``TRADINGAGENTS_*`` import-time state. Upstream ``TRADINGAGENTS_*`` variables
keep working and are simply layered beneath these (GUI vars win).
"""

from __future__ import annotations

import os

from app.instructions._upstream_runner import UpstreamRunner
from app.instructions.interface import GraphRunner

# Canonical analyst keys (upstream ``selected_analysts`` values) plus the
# ``*_analyst`` aliases used in deployment env for readability.
_ANALYST_ALIASES: dict[str, str] = {
    "market": "market",
    "market_analyst": "market",
    "social": "social",
    "social_analyst": "social",
    "news": "news",
    "news_analyst": "news",
    "fundamentals": "fundamentals",
    "fundamentals_analyst": "fundamentals",
}

_DEFAULT_ANALYSTS = ("market", "social", "news", "fundamentals")


def _env(name: str) -> str | None:
    """Return a stripped, non-empty env value or ``None`` (unset/blank = unset)."""
    value = os.environ.get(name)
    if value is None or not value.strip():
        return None
    return value.strip()


def _config_overrides_from_env() -> dict[str, object]:
    """Build the config-dict overrides from ``TA_WEBGUI_*`` deployment vars.

    Raises ``ValueError`` with a deployment-actionable message on malformed
    values; the run engine surfaces only the exception category to clients
    while the server log keeps the detail (error hygiene contract).
    """
    overrides: dict[str, object] = {}
    provider = _env("TA_WEBGUI_LLM_PROVIDER")
    if provider:
        overrides["llm_provider"] = provider
    quick = _env("TA_WEBGUI_QUICK_MODEL")
    if quick:
        overrides["quick_think_llm"] = quick
    deep = _env("TA_WEBGUI_DEEP_MODEL")
    if deep:
        overrides["deep_think_llm"] = deep
    rounds_raw = _env("TA_WEBGUI_MAX_DEBATE_ROUNDS")
    if rounds_raw:
        try:
            rounds = int(rounds_raw)
        except ValueError as exc:
            raise ValueError("TA_WEBGUI_MAX_DEBATE_ROUNDS must be an integer") from exc
        if rounds < 0:
            raise ValueError("TA_WEBGUI_MAX_DEBATE_ROUNDS must be >= 0")
        overrides["max_debate_rounds"] = rounds
    return overrides


def _selected_analysts_from_env() -> tuple[str, ...] | None:
    """Parse ``TA_WEBGUI_SELECTED_ANALYSTS`` (comma list) or ``None`` when unset."""
    raw = _env("TA_WEBGUI_SELECTED_ANALYSTS")
    if raw is None:
        return None
    analysts: list[str] = []
    for part in raw.split(","):
        key = _ANALYST_ALIASES.get(part.strip().lower())
        if key is None:
            valid = ", ".join(sorted(set(_ANALYST_ALIASES.values())))
            raise ValueError(
                "TA_WEBGUI_SELECTED_ANALYSTS contains an unknown analyst "
                f"{part.strip()!r}; valid: {valid}"
            )
        analysts.append(key)
    if not analysts:
        raise ValueError("TA_WEBGUI_SELECTED_ANALYSTS is empty; name at least one analyst")
    return tuple(analysts)


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
        from tradingagents.default_config import DEFAULT_CONFIG
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        # Copy, never mutate, the upstream default: other providers/tests in
        # this process may rely on pristine upstream defaults.
        config = dict(DEFAULT_CONFIG)
        config.update(_config_overrides_from_env())
        analysts = _selected_analysts_from_env() or _DEFAULT_ANALYSTS
        graph = TradingAgentsGraph(selected_analysts=analysts, config=config)
        return UpstreamRunner(graph, ticker, date, asset_type)
