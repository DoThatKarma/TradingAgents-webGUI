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
4. Depth presets (``depth=`` argument, ADR 0007): a request-level preset
   wins over all environment layers, but only on the exact keys
   ``DEPTH_PRESETS`` maps for it. Keys outside the preset keep the env
   precedence above. ``standard`` maps nothing, so the default depth is
   fully backward-compatible with pure-env deployments.

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

# --- Depth presets (ADR 0007) -------------------------------------------------
#
# A depth preset is the request-level top layer: it wins over every
# environment layer, but only on the exact keys it lists. ``standard`` maps
# nothing on purpose — the default depth keeps pure-env deployments
# byte-for-byte identical to the pre-depth behaviour.
#
# ``selected_analysts`` is resolved separately (upstream takes analysts as a
# constructor argument, not a config-dict key) but still belongs to the
# preset table; ``_apply_depth`` skips it when building the config dict.
_DEPTH_FOLLOWS_QUICK = object()  # sentinel: deep model becomes the quick model

DEPTH_PRESETS: dict[str, dict[str, object]] = {
    "fast": {
        "max_debate_rounds": 0,
        "max_risk_discuss_rounds": 0,
        "selected_analysts": ("market", "fundamentals"),
        "deep_think_llm": _DEPTH_FOLLOWS_QUICK,
        "news_article_limit": 10,
        "global_news_article_limit": 5,
        "global_news_lookback_days": 3,
    },
    "standard": {},
    "deep": {
        "max_debate_rounds": 3,
        "max_risk_discuss_rounds": 2,
        "news_article_limit": 30,
        "global_news_article_limit": 15,
        "global_news_lookback_days": 14,
        # Wider graph (more debate rounds) needs a higher node budget.
        "max_recur_limit": 150,
    },
}

# Provider-aware reasoning effort per preset (§D preset table). Applied to
# the single ``*_effort``/``*thinking_level`` config key matching the resolved
# ``llm_provider``; deployments on other providers keep their provider
# default. ``standard`` intentionally has no entry.
DEPTH_EFFORT: dict[str, str] = {"fast": "low", "deep": "high"}

_EFFORT_KEY_BY_PROVIDER: dict[str, str] = {
    "openai": "openai_reasoning_effort",
    "google": "google_thinking_level",
    "anthropic": "anthropic_effort",
}


def validate_depth(depth: str) -> str:
    """Return the canonical depth name or raise ``ValueError`` on unknowns."""
    if depth not in DEPTH_PRESETS:
        valid = ", ".join(sorted(DEPTH_PRESETS))
        raise ValueError(f"unknown depth {depth!r}; valid: {valid}")
    return depth


def _apply_depth(config: dict[str, object], depth: str) -> None:
    """Apply one depth preset as the top layer (its mapped keys only).

    ``selected_analysts`` is skipped here — it is consumed by
    :func:`resolve_selected_analysts`, never written into the config dict.
    """
    for key, value in DEPTH_PRESETS[depth].items():
        if key == "selected_analysts":
            continue
        if value is _DEPTH_FOLLOWS_QUICK:
            value = config.get("quick_think_llm")
        config[key] = value
    effort = DEPTH_EFFORT.get(depth)
    if effort is not None:
        provider = str(config.get("llm_provider", "")).lower()
        effort_key = _EFFORT_KEY_BY_PROVIDER.get(provider)
        if effort_key is not None:
            config[effort_key] = effort


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


def resolve_graph_config(depth: str | None = None) -> dict[str, object]:
    """Resolve the full ``TradingAgentsGraph`` config from the environment.

    Layering: copied upstream ``DEFAULT_CONFIG`` -> GUI baseline (skipped
    per-key where an explicit ``TA_WEBGUI_*`` or matching upstream
    ``TRADINGAGENTS_*`` choice exists) -> ``TA_WEBGUI_*`` overrides ->
    optional depth preset (ADR 0007; its mapped keys only). Raises
    ``ValueError`` on malformed ``TA_WEBGUI_*`` values and on unknown depths.
    ``depth=None`` (or ``"standard"``) reproduces the pre-depth behaviour.
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
    if depth is not None:
        validate_depth(depth)
        _apply_depth(config, depth)
    return config


def resolve_selected_analysts(depth: str | None = None) -> tuple[str, ...]:
    """Resolve the ``selected_analysts`` tuple from the environment.

    Upstream takes analysts as a dedicated constructor argument (not part of
    the config dict), so this is resolved separately and passed positionally
    by both adapters. Raises ``ValueError`` on unknown or empty values.

    A depth preset with a ``selected_analysts`` entry (``fast``, ADR 0007)
    replaces the env/default analyst set wholesale; analysts are one of the
    preset-mapped knobs, so the request-level preset wins over
    ``TA_WEBGUI_SELECTED_ANALYSTS``. ``standard`` keeps the env/default set.
    """
    if depth is not None:
        validate_depth(depth)
        preset_analysts = DEPTH_PRESETS[depth].get("selected_analysts")
        if preset_analysts is not None:
            return tuple(preset_analysts)  # type: ignore[arg-type]
    return _selected_analysts_from_env() or _DEFAULT_ANALYSTS
