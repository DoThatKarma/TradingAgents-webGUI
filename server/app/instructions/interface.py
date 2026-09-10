"""Instruction-layer contract (ADR 0002).

This module is the ONLY interface between the run engine and the instruction
adapters: the engine and API layers import exclusively from here, and only
modules under ``app.instructions`` may import ``ta_plugins``.

The contract is intentionally tiny:

* An :class:`InstructionProvider` turns run parameters into a
  :class:`GraphRunner` (one prepared analysis run).
* A :class:`GraphRunner` streams per-node state snapshots and finally reports
  the merged final state plus the processed trading signal.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol, runtime_checkable


class ProviderUnavailableError(RuntimeError):
    """An optional instruction provider is missing or import-broken."""


@runtime_checkable
class GraphRunner(Protocol):
    """A single prepared analysis run.

    ``stream()`` yields one full state snapshot (``dict``) after each graph
    node completes. ``finalize()`` returns ``(final_state, signal)`` where
    ``signal`` is the processed decision rating (an upstream 5-tier rating or
    ``"REVIEW"``).

    Lifecycle: drive ``stream()`` to exhaustion *or* call ``finalize()`` on a
    runner whose stream was never iterated. A stream that was closed early
    (cancelled run) cannot be finalized.
    """

    def stream(self) -> Iterator[dict[str, Any]]:
        """Yield one cumulative state snapshot per completed graph node."""
        ...

    def finalize(self) -> tuple[dict[str, Any], str]:
        """Return ``(final_state, signal)``; see the class docstring."""
        ...


@runtime_checkable
class InstructionProvider(Protocol):
    """Builds :class:`GraphRunner` instances for the run engine."""

    def build_runner(
        self,
        ticker: str,
        date: str,
        asset_type: str = "stock",
        instructions: str | None = None,
    ) -> GraphRunner:
        """Construct a runner for one analysis run."""
        ...
