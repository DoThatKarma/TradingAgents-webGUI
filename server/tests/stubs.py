"""Shared offline test doubles: canned snapshots and stub runners/factories."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from app.engine.runner import RunSpec
from app.instructions.interface import GraphRunner


def snapshot(
    *,
    market: str = "",
    sentiment: str = "",
    news: str = "",
    fundamentals: str = "",
    bull: str = "",
    bear: str = "",
    research_judge: str = "",
    investment_plan: str = "",
    trader_plan: str = "",
    aggressive: str = "",
    conservative: str = "",
    neutral: str = "",
    risk_judge: str = "",
    final_decision: str = "",
) -> dict[str, Any]:
    """Build one cumulative AgentState-shaped snapshot."""
    return {
        "market_report": market,
        "sentiment_report": sentiment,
        "news_report": news,
        "fundamentals_report": fundamentals,
        "investment_debate_state": {
            "bull_history": bull,
            "bear_history": bear,
            "judge_decision": research_judge,
        },
        "investment_plan": investment_plan,
        "trader_investment_plan": trader_plan,
        "risk_debate_state": {
            "aggressive_history": aggressive,
            "conservative_history": conservative,
            "neutral_history": neutral,
            "judge_decision": risk_judge,
        },
        "final_trade_decision": final_decision,
    }


def standard_snapshots() -> list[dict[str, Any]]:
    """Canned cumulative snapshots covering the full pipeline, in order."""
    return [
        snapshot(market="Market: NVDA technicals strong"),
        snapshot(
            market="Market: NVDA technicals strong",
            sentiment="Sentiment: bullish",
        ),
        snapshot(
            market="Market: NVDA technicals strong",
            sentiment="Sentiment: bullish",
            news="News: earnings beat",
        ),
        snapshot(
            market="Market: NVDA technicals strong",
            sentiment="Sentiment: bullish",
            news="News: earnings beat",
            fundamentals="Fundamentals: revenue growth",
        ),
        snapshot(
            market="Market: NVDA technicals strong",
            sentiment="Sentiment: bullish",
            news="News: earnings beat",
            fundamentals="Fundamentals: revenue growth",
            bull="Bull: growth justifies entry",
        ),
        snapshot(
            market="Market: NVDA technicals strong",
            sentiment="Sentiment: bullish",
            news="News: earnings beat",
            fundamentals="Fundamentals: revenue growth",
            bull="Bull: growth justifies entry",
            bear="Bear: valuation stretched",
        ),
        snapshot(
            market="Market: NVDA technicals strong",
            sentiment="Sentiment: bullish",
            news="News: earnings beat",
            fundamentals="Fundamentals: revenue growth",
            bull="Bull: growth justifies entry",
            bear="Bear: valuation stretched",
            research_judge="Judge: proceed",
            investment_plan="Plan: accumulate",
        ),
        snapshot(
            market="Market: NVDA technicals strong",
            sentiment="Sentiment: bullish",
            news="News: earnings beat",
            fundamentals="Fundamentals: revenue growth",
            bull="Bull: growth justifies entry",
            bear="Bear: valuation stretched",
            research_judge="Judge: proceed",
            investment_plan="Plan: accumulate",
            trader_plan="Trader: buy 100 shares",
        ),
        snapshot(
            market="Market: NVDA technicals strong",
            sentiment="Sentiment: bullish",
            news="News: earnings beat",
            fundamentals="Fundamentals: revenue growth",
            bull="Bull: growth justifies entry",
            bear="Bear: valuation stretched",
            research_judge="Judge: proceed",
            investment_plan="Plan: accumulate",
            trader_plan="Trader: buy 100 shares",
            aggressive="Aggressive: upside dominates",
        ),
        snapshot(
            market="Market: NVDA technicals strong",
            sentiment="Sentiment: bullish",
            news="News: earnings beat",
            fundamentals="Fundamentals: revenue growth",
            bull="Bull: growth justifies entry",
            bear="Bear: valuation stretched",
            research_judge="Judge: proceed",
            investment_plan="Plan: accumulate",
            trader_plan="Trader: buy 100 shares",
            aggressive="Aggressive: upside dominates",
            conservative="Conservative: downside contained",
        ),
        snapshot(
            market="Market: NVDA technicals strong",
            sentiment="Sentiment: bullish",
            news="News: earnings beat",
            fundamentals="Fundamentals: revenue growth",
            bull="Bull: growth justifies entry",
            bear="Bear: valuation stretched",
            research_judge="Judge: proceed",
            investment_plan="Plan: accumulate",
            trader_plan="Trader: buy 100 shares",
            aggressive="Aggressive: upside dominates",
            conservative="Conservative: downside contained",
            neutral="Neutral: balanced",
        ),
        snapshot(
            market="Market: NVDA technicals strong",
            sentiment="Sentiment: bullish",
            news="News: earnings beat",
            fundamentals="Fundamentals: revenue growth",
            bull="Bull: growth justifies entry",
            bear="Bear: valuation stretched",
            research_judge="Judge: proceed",
            investment_plan="Plan: accumulate",
            trader_plan="Trader: buy 100 shares",
            aggressive="Aggressive: upside dominates",
            conservative="Conservative: downside contained",
            neutral="Neutral: balanced",
            risk_judge="PM: approve",
            final_decision="final decision: buy",
        ),
    ]


class StubRunner:
    """GraphRunner over canned snapshots; records finalize calls."""

    def __init__(self, snapshots: list[dict[str, Any]], signal: str = "Buy") -> None:
        self._snapshots = list(snapshots)
        self._signal = signal
        self.finalize_calls = 0

    def stream(self) -> Iterator[dict[str, Any]]:
        yield from self._snapshots

    def finalize(self) -> tuple[dict[str, Any], str]:
        self.finalize_calls += 1
        final: dict[str, Any] = {}
        for snap in self._snapshots:
            final.update(snap)
        return final, self._signal


class StubGraphFactory:
    """Injectable graph factory returning one shared stub runner."""

    def __init__(self, runner: StubRunner | None = None) -> None:
        self.runner = runner or StubRunner(standard_snapshots(), signal="Buy")
        self.specs: list[RunSpec] = []

    def __call__(self, spec: RunSpec) -> GraphRunner:
        self.specs.append(spec)
        return self.runner
