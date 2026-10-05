"""Unit tests for the `is_anomaly` heuristic on the search-hits surface.

A hit gets flagged when its similarity is more than two stdevs
below the mean similarity in the result set; structured-only searches
have no signal and never flag.

The heuristic lives on `api.admin.search_queries._flag_anomalies` and
mutates a list of `SearchHit`s in place. We exercise it directly here
because the integration tests need a live DB + embedding endpoint to
generate similarities.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from api.admin.search_queries import _flag_anomalies  # pyright: ignore[reportPrivateUsage]
from api.schemas.admin_search import SearchHit


def _hit(similarity: float | None) -> SearchHit:
    return SearchHit(
        id=uuid.uuid4(),
        damage_class="partial",
        description="",
        description_en=None,
        infra_type=None,
        infra_name=None,
        debris=None,
        building_id=None,
        building_name=None,
        location=None,
        map_point=None,
        photo_path="x/x.jpg",
        created_at=datetime.now(UTC),
        similarity=similarity,
    )


def test_no_signal_means_no_flag() -> None:
    """Structured-only searches leave every row at is_anomaly=False."""
    hits = [_hit(None), _hit(None), _hit(None), _hit(None)]
    _flag_anomalies(hits)
    assert not any(h.is_anomaly for h in hits)


def test_outlier_below_mean_is_flagged() -> None:
    """One row two-plus stdevs below the mean trips the flag."""
    similarities = [0.90, 0.88, 0.89, 0.91, 0.92, 0.10]
    hits = [_hit(s) for s in similarities]
    _flag_anomalies(hits)
    assert hits[-1].is_anomaly is True
    assert all(not h.is_anomaly for h in hits[:-1])


def test_uniform_set_has_no_anomalies() -> None:
    """Zero variance means we skip flagging entirely."""
    hits = [_hit(0.8) for _ in range(10)]
    _flag_anomalies(hits)
    assert not any(h.is_anomaly for h in hits)


def test_too_few_hits_skips_flagging() -> None:
    """Under 3 hits we have no meaningful stdev; skip silently."""
    hits = [_hit(0.9), _hit(0.1)]
    _flag_anomalies(hits)
    assert not any(h.is_anomaly for h in hits)
