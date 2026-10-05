"""Number formatting shared by the report renderer and the narrative prompts."""

from __future__ import annotations


def fmt_coverage_pct(pct: float) -> str:
    """Format a coverage rate; a tiny nonzero rate shows as "<0.1%" instead of "0.0%"."""
    if pct <= 0.0:
        return "0%"
    if pct < 0.1:
        return "<0.1%"
    return f"{pct:.1f}%"


def fmt_usd(value: float | None) -> str:
    """Compact USD: $1.2M / $850K / $1.4B."""
    if value is None:
        return "n/a"
    v = float(value)
    if v >= 1_000_000_000:
        return f"${v / 1_000_000_000:.1f}B"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.0f}K"
    return f"${v:,.0f}"
