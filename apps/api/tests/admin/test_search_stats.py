"""Unit tests for the search-stats prompt-block renderer.

The chat backend pins the rendered block into the prompt so breadth questions
answer from real aggregates rather than the K-sample. These tests guard the
rendered shape against accidental drift.
"""

from __future__ import annotations

from typing import Any

from api.ai.chat import render_stats_block
from api.schemas.admin_search import SearchStats


def _stats(**overrides: Any) -> SearchStats:
    fields: dict[str, Any] = {
        "total": 1,
        "severity": {"complete": 0, "partial": 0, "minimal": 0},
        "last_24h": 1,
        "last_hour": 1,
        "top_infra": None,
        "top_infra_count": 0,
        "debris_yes": 0,
        "debris_known": 0,
        "with_building": 0,
        "with_gps": 0,
        "with_geocode": 0,
        "unmapped": 1,
    }
    fields.update(overrides)
    return SearchStats(**fields)


def test_render_stats_block_quotes_total_match_count() -> None:
    stats = _stats(
        severity={"complete": 1, "partial": 0, "minimal": 0},
        top_infra="school",
        top_infra_count=1,
    )
    block = render_stats_block(stats, total_match_count=42)
    # Truncated sample is honest about the gap so the LLM sees both.
    assert "42" in block
    assert "1" in block  # the sample size
    assert "complete: 1" in block
    assert "school" in block


def test_render_stats_block_drops_truncation_note_when_not_capped() -> None:
    stats = _stats(severity={"complete": 0, "partial": 1, "minimal": 0})
    block = render_stats_block(stats, total_match_count=1)
    assert "Total reports: 1" in block
