"""Report export: assemble a completed run's stored data into a Markdown file.

Pure presentation layer over the job manager's exported material. The event
log is the single source of truth: analyst ``report`` events plus the final
``decision`` event; the run spec provides the metadata header. Client-facing
output never includes server-side error details (ADR 0005).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

# Section order mirrors the analyst pipeline; unexpected future keys are
# appended after these (defensive forward compatibility).
_SECTIONS: tuple[tuple[str, str], ...] = (
    ("market_report", "Market Analyst Report"),
    ("sentiment_report", "Sentiment Analyst Report"),
    ("news_report", "News Analyst Report"),
    ("fundamentals_report", "Fundamentals Analyst Report"),
)

_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9.\-]")


def report_filename(ticker: str, date: str) -> str:
    """Build the download filename; keep alnum, dot, dash only."""
    safe_ticker = _FILENAME_SAFE.sub("", ticker) or "run"
    safe_date = _FILENAME_SAFE.sub("", date) or "undated"
    return f"tradingagents-{safe_ticker}-{safe_date}.md"


def _format_timestamp(ts: float | None) -> str:
    if ts is None:
        return "unknown"
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")


def build_report_markdown(material: dict[str, Any]) -> str:
    """Render one completed run's metadata, reports, and decision as Markdown."""
    reports: dict[str, str] = material["reports"]
    decision: dict[str, Any] | None = material["decision"]
    lines: list[str] = [
        "# TradingAgents Analysis Report",
        "",
        f"- **Ticker:** {material['ticker']}",
        f"- **Analysis date:** {material['date']}",
        f"- **Asset type:** {material['asset_type']}",
        f"- **Provider:** {material['provider']}",
        f"- **Custom instructions:** {'yes' if material['has_instructions'] else 'no'}",
        f"- **Completed at:** {_format_timestamp(material['completed_at'])}",
    ]
    titles = dict(_SECTIONS)
    ordered = [key for key, _ in _SECTIONS if key in reports]
    extras = sorted(key for key in reports if key not in titles)
    for key in ordered + extras:
        title = titles.get(key, key.replace("_", " ").title())
        lines += ["", f"## {title}", "", reports[key].rstrip()]
    if decision is not None:
        signal = str(decision.get("signal", ""))
        lines += ["", "## Final Decision", "", f"**Signal:** {signal}".rstrip()]
        text = str(decision.get("decision", "")).rstrip()
        if text:
            lines += ["", text]
    lines.append("")
    return "\n".join(lines)
