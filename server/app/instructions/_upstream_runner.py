"""Shared driver for one upstream analysis-graph run.

Lives under ``app.instructions`` because only adapters construct runners; the
engine consumes the :class:`~app.instructions.interface.GraphRunner` protocol
without knowing this implementation.

The run loop replicates the upstream CLI streaming path (``cli/main.py``):
resolve the instrument context, build the initial state, stream per-node
snapshots in ``values`` mode, keep checkpoint teardown in a ``finally`` block,
and merge chunks into the final state.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any


class UpstreamRunner:
    """Drives one upstream trading-graph run as a GraphRunner."""

    def __init__(self, graph: Any, ticker: str, date: str, asset_type: str = "stock") -> None:
        self._graph = graph
        self._ticker = ticker
        self._date = date
        self._asset_type = asset_type
        self._chunks: list[dict[str, Any]] = []
        self._result: tuple[dict[str, Any], str] | None = None
        self._started = False
        self._drained = False
        self._aborted = False

    def stream(self) -> Iterator[dict[str, Any]]:
        """Yield one cumulative state snapshot per completed graph node."""
        self._started = True
        graph = self._graph
        instrument_context = graph.resolve_instrument_context(self._ticker, self._asset_type)
        init_state = graph.propagator.create_initial_state(
            self._ticker,
            self._date,
            asset_type=self._asset_type,
            instrument_context=instrument_context,
        )
        args = graph.propagator.get_graph_args()
        thread_id = graph.begin_checkpoint(self._ticker, self._date, self._asset_type)
        if thread_id is not None:
            config = args.setdefault("config", {})
            config.setdefault("configurable", {})["thread_id"] = thread_id
        completed = False
        try:
            for chunk in graph.graph.stream(graph.checkpoint_input(init_state), **args):
                self._chunks.append(chunk)
                yield chunk
            completed = True
            graph.clear_checkpoint_on_success(self._ticker, self._date, self._asset_type)
        finally:
            graph.end_checkpoint()
            if not completed:
                self._aborted = True
        self._drained = True

    def finalize(self) -> tuple[dict[str, Any], str]:
        """Return ``(final_state, signal)`` for this run.

        Drives the run to completion when the stream was never iterated.
        Raises :class:`RuntimeError` for streams that did not complete
        (cancelled or failed) or are still open.
        """
        if self._result is None:
            if self._aborted:
                raise RuntimeError("run stream did not complete; finalize() is unavailable")
            if self._started and not self._drained:
                raise RuntimeError("stream is still open; exhaust it before finalize()")
            if not self._started:
                for _ in self.stream():
                    pass
            final_state: dict[str, Any] = {}
            for chunk in self._chunks:
                final_state.update(chunk)
            if "final_trade_decision" not in final_state:
                raise RuntimeError("run produced no final trade decision; stream was empty")
            self._result = (
                final_state,
                self._graph.process_signal(final_state["final_trade_decision"]),
            )
        return self._result
