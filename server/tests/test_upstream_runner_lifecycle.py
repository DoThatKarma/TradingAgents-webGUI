"""UpstreamRunner lifecycle over a stub upstream graph (offline, ADR 0003)."""

from __future__ import annotations

from typing import Any

import pytest

from app.instructions._upstream_runner import UpstreamRunner
from tests.stubs import snapshot


class _StubPropagator:
    def create_initial_state(
        self,
        ticker: str,
        date: str,
        asset_type: str = "stock",
        instrument_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {"ticker": ticker, "date": date, "asset_type": asset_type}

    def get_graph_args(self) -> dict[str, Any]:
        return {}


class _InnerGraph:
    """The ``graph.graph`` attribute: stream() over canned chunks."""

    def __init__(self, owner: StubUpstreamGraph) -> None:
        self._owner = owner

    def stream(self, state: dict[str, Any], **kwargs: Any) -> Any:
        self._owner.stream_states.append(state)
        self._owner.stream_kwargs.append(kwargs)
        for index, chunk in enumerate(self._owner.chunks):
            if self._owner.fail_after is not None and index >= self._owner.fail_after:
                raise RuntimeError("upstream exploded")
            yield chunk


class StubUpstreamGraph:
    """Minimal double for the upstream TradingAgentsGraph surface."""

    def __init__(
        self,
        chunks: list[dict[str, Any]],
        *,
        fail_after: int | None = None,
        signal: str = "buy",
    ) -> None:
        self.chunks = list(chunks)
        self.fail_after = fail_after
        self.signal = signal
        self.propagator = _StubPropagator()
        self.graph = _InnerGraph(self)
        self.checkpoint_calls: list[str] = []
        self.stream_states: list[dict[str, Any]] = []
        self.stream_kwargs: list[dict[str, Any]] = []
        self.cleared_success = False

    def resolve_instrument_context(self, ticker: str, asset_type: str) -> dict[str, Any]:
        return {"symbol": ticker, "asset_type": asset_type}

    def begin_checkpoint(self, ticker: str, date: str, asset_type: str) -> str:
        self.checkpoint_calls.append("begin")
        return "thread-1"

    def checkpoint_input(self, state: dict[str, Any]) -> dict[str, Any]:
        self.checkpoint_calls.append("checkpoint_input")
        marked = dict(state)
        marked["checkpointed"] = True
        return marked

    def clear_checkpoint_on_success(self, ticker: str, date: str, asset_type: str) -> None:
        self.checkpoint_calls.append("clear")
        self.cleared_success = True

    def end_checkpoint(self) -> None:
        self.checkpoint_calls.append("end")

    def process_signal(self, final_state: dict[str, Any]) -> str:
        return self.signal


def _runner(
    graph: StubUpstreamGraph, ticker: str = "NVDA", date: str = "2025-01-10"
) -> UpstreamRunner:
    return UpstreamRunner(graph, ticker, date, "stock")


def test_normal_run_merges_snapshots_and_finalizes() -> None:
    chunks = [
        snapshot(market="Market: a"),
        snapshot(market="Market: a", final_decision="final decision: buy"),
    ]
    graph = StubUpstreamGraph(chunks)
    runner = _runner(graph)

    seen = list(runner.stream())
    assert seen == chunks

    final_state, signal = runner.finalize()
    assert final_state["market_report"] == "Market: a"
    assert final_state["final_trade_decision"] == "final decision: buy"
    assert signal == "buy"
    assert graph.cleared_success is True
    assert graph.checkpoint_calls.count("end") == 1  # end_checkpoint in finally


def test_abort_mid_stream_cannot_finalize() -> None:
    graph = StubUpstreamGraph([snapshot(market="Market: a"), snapshot(market="Market: b")])
    runner = _runner(graph)

    iterator = runner.stream()
    next(iterator)
    iterator.close()  # client abort: GeneratorExit inside stream()

    with pytest.raises(RuntimeError, match="did not complete"):
        runner.finalize()
    assert graph.checkpoint_calls.count("end") == 1  # teardown still ran
    assert graph.cleared_success is False


def test_finalize_drives_never_started_stream() -> None:
    graph = StubUpstreamGraph(
        [snapshot(market="Market: a", final_decision="final decision: hold")],
        signal="hold",
    )
    runner = _runner(graph)

    final_state, signal = runner.finalize()  # stream() never called
    assert final_state["market_report"] == "Market: a"
    assert signal == "hold"
    assert len(graph.stream_states) == 1
    assert graph.checkpoint_calls.count("end") == 1


def test_end_checkpoint_runs_when_upstream_fails() -> None:
    graph = StubUpstreamGraph(
        [snapshot(market="Market: a"), snapshot(market="Market: b")], fail_after=1
    )
    runner = _runner(graph)

    with pytest.raises(RuntimeError, match="upstream exploded"):
        list(runner.stream())

    assert graph.checkpoint_calls.count("end") == 1  # finally-block teardown
    assert graph.cleared_success is False


def test_checkpoint_input_pass_through() -> None:
    graph = StubUpstreamGraph([snapshot(final_decision="final decision: buy")])
    runner = _runner(graph, ticker="TSLA", date="2025-02-01")

    list(runner.stream())

    passed = graph.stream_states[0]
    assert passed["checkpointed"] is True
    assert passed["ticker"] == "TSLA"
    assert passed["date"] == "2025-02-01"
    # thread_id from begin_checkpoint lands in the graph args config.
    config = graph.stream_kwargs[0]["config"]
    assert config["configurable"]["thread_id"] == "thread-1"


def test_empty_stream_finalize_raises_runtime_error() -> None:
    graph = StubUpstreamGraph([])
    runner = _runner(graph)

    with pytest.raises(RuntimeError, match="stream was empty"):
        runner.finalize()
