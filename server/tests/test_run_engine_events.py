"""RunEngine event mapping over a stub graph stream (offline, ADR 0003)."""

from __future__ import annotations

from app.engine.runner import RunEngine, RunSpec
from tests.stubs import StubGraphFactory, StubRunner, standard_snapshots

EXPECTED_STATUS_SEQUENCE = [
    ("Market Analyst", "in_progress"),
    ("Market Analyst", "completed"),
    ("Sentiment Analyst", "in_progress"),
    ("Sentiment Analyst", "completed"),
    ("News Analyst", "in_progress"),
    ("News Analyst", "completed"),
    ("Fundamentals Analyst", "in_progress"),
    ("Fundamentals Analyst", "completed"),
    ("Bull Researcher", "in_progress"),
    ("Bull Researcher", "completed"),
    ("Bear Researcher", "in_progress"),
    ("Bear Researcher", "completed"),
    ("Research Manager", "in_progress"),
    ("Research Manager", "completed"),
    ("Trader", "in_progress"),
    ("Trader", "completed"),
    ("Aggressive Analyst", "in_progress"),
    ("Aggressive Analyst", "completed"),
    ("Conservative Analyst", "in_progress"),
    ("Conservative Analyst", "completed"),
    ("Neutral Analyst", "in_progress"),
    ("Neutral Analyst", "completed"),
    ("Portfolio Manager", "in_progress"),
    ("Portfolio Manager", "completed"),
]


def test_engine_emits_ordered_statuses_reports_and_decision() -> None:
    factory = StubGraphFactory()
    engine = RunEngine(graph_factory=factory)

    events = list(engine.run(RunSpec(ticker="NVDA", date="2025-01-10")))

    statuses = [(e["agent"], e["status"]) for e in events if e["type"] == "agent_status"]
    assert statuses == EXPECTED_STATUS_SEQUENCE

    reports = [e for e in events if e["type"] == "report"]
    assert [e["key"] for e in reports] == [
        "market_report",
        "sentiment_report",
        "news_report",
        "fundamentals_report",
    ]
    assert reports[0]["content"] == "Market: NVDA technicals strong"
    assert reports[3]["content"] == "Fundamentals: revenue growth"

    decision = events[-1]
    assert decision["type"] == "decision"
    assert decision["signal"] == "Buy"
    assert decision["decision"] == "final decision: buy"

    assert events[0] == {
        "type": "agent_status",
        "agent": "Market Analyst",
        "status": "in_progress",
    }


def test_engine_passes_spec_to_graph_factory() -> None:
    factory = StubGraphFactory()
    engine = RunEngine(graph_factory=factory)

    list(engine.run(RunSpec(ticker="NVDA", date="2025-01-10", asset_type="crypto")))

    assert len(factory.specs) == 1
    assert factory.specs[0].ticker == "NVDA"
    assert factory.specs[0].asset_type == "crypto"


def test_engine_repeats_report_only_on_change() -> None:
    # Every snapshot carries the same market report; it must be emitted once.
    snapshots = [dict(standard_snapshots()[0]) for _ in range(3)]
    engine = RunEngine(graph_factory=StubGraphFactory(StubRunner(snapshots, signal="Hold")))

    events = list(engine.run(RunSpec(ticker="NVDA", date="2025-01-10")))

    assert [e["key"] for e in events if e["type"] == "report"] == ["market_report"]
    assert events[-1]["signal"] == "Hold"
