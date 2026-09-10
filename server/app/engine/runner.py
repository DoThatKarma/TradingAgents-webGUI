"""Run engine: maps graph state snapshots to UI events.

The engine consumes only the :class:`~app.instructions.interface.GraphRunner`
protocol (ADR 0002) — it never imports the upstream graph or ``ta_plugins``.
The graph factory is injectable so offline tests can feed canned state
snapshots without network access or API keys (ADR 0003).

Event mapping:
- ``agent_status``: ordered pipeline stages (analysts -> research debate ->
  research manager -> trader -> risk debate -> portfolio manager), derived
  from evidence in each cumulative state snapshot.
- ``report``: analyst reports (``market_report``, ``sentiment_report``,
  ``news_report``, ``fundamentals_report``) on first content and on change.
- ``decision``: emitted once after the stream completes, from the final
  state plus the processed signal.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from app.instructions.interface import GraphRunner


class RunCancelled(Exception):
    """Raised between stream chunks when a run is cancelled."""


@dataclass
class RunSpec:
    """Parameters of one analysis run.

    ``effective_provider`` is stamped by the default graph factory once the
    instruction factory resolved (and possibly fell back from) the requested
    provider; the job manager records it for status reporting.
    """

    ticker: str
    date: str
    asset_type: str = "stock"
    instructions: str | None = None
    provider: str = "direct"
    effective_provider: str | None = None


REPORT_KEYS: tuple[str, ...] = (
    "market_report",
    "sentiment_report",
    "news_report",
    "fundamentals_report",
)

# Pipeline stages in UI order with the state paths that prove completion.
# Dotted paths descend into nested debate states.
_STAGE_EVIDENCE: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Market Analyst", ("market_report",)),
    ("Sentiment Analyst", ("sentiment_report",)),
    ("News Analyst", ("news_report",)),
    ("Fundamentals Analyst", ("fundamentals_report",)),
    ("Bull Researcher", ("investment_debate_state.bull_history",)),
    ("Bear Researcher", ("investment_debate_state.bear_history",)),
    ("Research Manager", ("investment_debate_state.judge_decision", "investment_plan")),
    ("Trader", ("trader_investment_plan",)),
    ("Aggressive Analyst", ("risk_debate_state.aggressive_history",)),
    ("Conservative Analyst", ("risk_debate_state.conservative_history",)),
    ("Neutral Analyst", ("risk_debate_state.neutral_history",)),
    ("Portfolio Manager", ("risk_debate_state.judge_decision", "final_trade_decision")),
)


def _lookup(snapshot: dict[str, Any], path: str) -> Any:
    current: Any = snapshot
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


class _PipelineTracker:
    """Derives ordered agent_status / report events from state snapshots."""

    def __init__(self) -> None:
        self._completed: set[str] = set()
        self._announced: set[str] = set()
        self._reported: dict[str, str] = {}

    def start(self) -> dict[str, Any]:
        first = _STAGE_EVIDENCE[0][0]
        self._announced.add(first)
        return {"type": "agent_status", "agent": first, "status": "in_progress"}

    def update(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for name, evidence in _STAGE_EVIDENCE:
            if name in self._completed:
                continue
            if any(_lookup(snapshot, path) for path in evidence):
                self._completed.add(name)
                events.append({"type": "agent_status", "agent": name, "status": "completed"})
        for name, _evidence in _STAGE_EVIDENCE:
            if name not in self._completed and name not in self._announced:
                self._announced.add(name)
                events.append({"type": "agent_status", "agent": name, "status": "in_progress"})
                break
        return events

    def reports(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for key in REPORT_KEYS:
            content = snapshot.get(key) or ""
            if content and self._reported.get(key) != content:
                self._reported[key] = content
                events.append({"type": "report", "key": key, "content": content})
        return events

    @staticmethod
    def decision(final_state: dict[str, Any], signal: str) -> dict[str, Any]:
        return {
            "type": "decision",
            "signal": signal,
            "decision": final_state.get("final_trade_decision", ""),
        }


GraphFactory = Callable[[RunSpec], GraphRunner]


def _default_graph_factory(spec: RunSpec) -> GraphRunner:
    # Imported lazily so importing this module never loads the instruction
    # adapters, the upstream graph, or ta_plugins (import-guard test).
    from app.instructions.factory import get_provider

    provider = get_provider(spec.provider, spec.instructions)
    # Stamp the resolved provider (after any fallback) so the manager can
    # report the effective provider per job (review fix S1).
    spec.effective_provider = getattr(provider, "name", spec.provider)
    return provider.build_runner(spec.ticker, spec.date, spec.asset_type, spec.instructions)


class RunEngine:
    """Drives a GraphRunner and yields UI events.

    ``graph_factory`` is injectable for offline tests (ADR 0003). It is
    invoked *eagerly* by :meth:`run`, which lets callers serialize runner
    construction (the job manager holds its provider lock around the call).
    ``should_cancel`` is consulted between stream chunks; when it returns
    True the engine raises :class:`RunCancelled` and closes the runner's
    stream so underlying resources are released.
    """

    def __init__(self, graph_factory: GraphFactory | None = None) -> None:
        self._graph_factory: GraphFactory = graph_factory or _default_graph_factory

    def run(
        self,
        spec: RunSpec,
        should_cancel: Callable[[], bool] | None = None,
    ) -> Iterator[dict[str, Any]]:
        runner = self._graph_factory(spec)
        return self._drive(runner, should_cancel)

    def _drive(
        self,
        runner: GraphRunner,
        should_cancel: Callable[[], bool] | None,
    ) -> Iterator[dict[str, Any]]:
        tracker = _PipelineTracker()
        yield tracker.start()
        stream = runner.stream()
        try:
            for snapshot in stream:
                if should_cancel is not None and should_cancel():
                    raise RunCancelled
                yield from tracker.update(snapshot)
                yield from tracker.reports(snapshot)
            # A cancel racing the final chunk must still win: re-check once
            # after the stream loop, before finalize() (review fix S2).
            if should_cancel is not None and should_cancel():
                raise RunCancelled
            final_state, signal = runner.finalize()
            yield tracker.decision(final_state, signal)
        finally:
            close = getattr(stream, "close", None)
            if close is not None:
                close()
