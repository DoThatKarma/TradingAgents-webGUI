"""Instruction-provider registry and lookup.

``get_provider(name)`` is the single entry point the rest of the backend uses
to obtain an :class:`InstructionProvider`. The ``ta_plugins`` availability
check is a lazy import probe so importing this module has no side effects and
never loads ``ta_plugins`` unless it is actually requested.
"""

from __future__ import annotations

import logging

from app.instructions.adapters.direct import DirectProvider
from app.instructions.adapters.ta_plugins_adapter import TAPluginsProvider
from app.instructions.interface import InstructionProvider, ProviderUnavailableError

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, type] = {
    "direct": DirectProvider,
    "ta_plugins": TAPluginsProvider,
}


def available_providers() -> list[str]:
    """Return the registered provider names."""
    return sorted(_REGISTRY)


def get_provider(name: str = "direct", instructions: str | None = None) -> InstructionProvider:
    """Instantiate the named provider.

    Fallback semantics (ADR 0002): when the plugin layer is missing or
    import-broken, ``ta_plugins`` falls back to ``direct`` — with a warning —
    as long as **no custom instructions** were requested, because the GUI must
    keep working without the plugin framework.

    Requesting ``ta_plugins`` *with* instructions while the plugin layer is
    unavailable raises :class:`ProviderUnavailableError`: silently dropping
    user-authored instructions would run a different analysis than the one
    the user asked for. Install ``ta-plugins`` or switch to ``direct`` without
    instructions instead.
    """
    if name not in _REGISTRY:
        raise ValueError(
            f"unknown provider {name!r}; available: {', '.join(available_providers())}"
        )
    if name == "ta_plugins":
        try:
            import ta_plugins  # noqa: F401
        except Exception as exc:  # import-broken covers any import-time failure
            if instructions:
                raise ProviderUnavailableError(
                    "provider 'ta_plugins' is unavailable; install ta-plugins, or use "
                    "provider 'direct' without custom instructions"
                ) from exc
            logger.warning(
                "ta_plugins unavailable (%s); falling back to the 'direct' provider",
                exc,
            )
            name = "direct"
    return _REGISTRY[name]()
