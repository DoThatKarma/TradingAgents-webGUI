"""Shared graph-config resolution for all instruction providers.

Single source of truth for turning the process environment into a
``TradingAgentsGraph`` config dict (plus the selected analyst set). Both the
``direct`` adapter and the ``ta_plugins`` adapter resolve their config through
this module, so custom-instruction runs get exactly the same LLM stack as
plain runs.

Resolution precedence (lowest to highest):

1. GUI baseline (``_GUI_BASELINE``): with no deployment vars set at all, the
   GUI ships an opinionated LLM stack (OpenRouter + ``z-ai/glm-5.3-flash`` for
   both roles) so a deployment only needs to provide ``OPENROUTER_API_KEY``.
2. Upstream ``TRADINGAGENTS_*`` env vars: an explicit upstream import-time
   choice wins over the baseline for the same key.
3. ``TA_WEBGUI_*`` overrides: always win per-key (documented in the root
   README).

Everything else starts from a *copied* upstream ``DEFAULT_CONFIG``; the
upstream default is never mutated (other providers/tests may rely on pristine
upstream defaults).

This module is deliberately dependency-free beyond ``os``: it never imports
``ta_plugins`` and never imports ``tradingagents`` at module import time (ADR
0003 — importing the adapters must not build LLM clients or require API
keys). The ``DEFAULT_CONFIG`` import happens lazily inside
:func:`resolve_graph_config`.
"""

from __future__ import annotations

import os

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

# Opinionated GUI baseline (lowest precedence): applied when neither a
# ``TA_WEBGUI_*`` var nor the matching upstream ``TRADINGAGENTS_*`` var is set.
_GUI_BASELINE: dict[str, object] = {
    "llm_provider": "openrouter",
    "quick_think_llm": "z-ai/glm-5.3-flash",
    "deep_think_llm": "z-ai/glm-5.3-flash",
}

# Upstream import-time env vars for the same keys; when one is set the user
# made an explicit upstream choice the baseline must not override.
_UPSTREAM_BASELINE_ENV: dict[str, str] = {
    "llm_provider": "TRADINGAGENTS_LLM_PROVIDER",
    "quick_think_llm": "TRADINGAGENTS_QUICK_THINK_LLM",
    "deep_think_llm": "TRADINGAGENTS_DEEP_THINK_LLM",
}


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


def resolve_graph_config() -> dict[str, object]:
    """Resolve the full ``TradingAgentsGraph`` config from the environment.

    Layering: copied upstream ``DEFAULT_CONFIG`` -> GUI baseline (skipped
    per-key where an explicit ``TA_WEBGUI_*`` or matching upstream
    ``TRADINGAGENTS_*`` choice exists) -> ``TA_WEBGUI_*`` overrides. Raises
    ``ValueError`` on malformed ``TA_WEBGUI_*`` values.
    """
    from tradingagents.default_config import DEFAULT_CONFIG

    # Copy, never mutate, the upstream default: other providers/tests in
    # this process may rely on pristine upstream defaults.
    config: dict[str, object] = dict(DEFAULT_CONFIG)
    overrides = _config_overrides_from_env()
    for key, baseline in _GUI_BASELINE.items():
        if key in overrides:
            continue  # explicit TA_WEBGUI_* choice wins
        if os.environ.get(_UPSTREAM_BASELINE_ENV[key]):
            continue  # explicit upstream TRADINGAGENTS_* choice wins
        config[key] = baseline
    config.update(overrides)
    return config


def resolve_selected_analysts() -> tuple[str, ...]:
    """Resolve the ``selected_analysts`` tuple from the environment.

    Upstream takes analysts as a dedicated constructor argument (not part of
    the config dict), so this is resolved separately and passed positionally
    by both adapters. Raises ``ValueError`` on unknown or empty values.
    """
    return _selected_analysts_from_env() or _DEFAULT_ANALYSTS
