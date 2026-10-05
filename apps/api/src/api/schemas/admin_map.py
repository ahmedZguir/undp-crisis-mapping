"""Admin map: SearchRequest filters plus viewport and zoom.

Zoomed out, the server returns grid clusters with exact counts; zoomed in, the
individual points. Semantic-query clusters aggregate only the top matches and
set capped.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from api.schemas.admin_search import LocationSource, SearchRequest
from api.schemas.common import DamageClass, LocationOut


class MapRequest(SearchRequest):
    """bbox (w, s, e, n) is always applied, ANDed with location."""

    bbox: tuple[float, float, float, float]
    zoom: float = Field(ge=0, le=24)
    # No-op without a query, since anomalies are defined by similarity.
    anomalies_only: bool = False


class MapClusterCell(BaseModel):
    lat: float
    lng: float
    total: int
    minimal: int
    partial: int
    complete: int
    # 1 means all reports share one coordinate, so zooming will never split the cell.
    distinct_point_count: int
    # Set only when total == 1, so a lone report draws as a normal pin.
    report_id: uuid.UUID | None = None
    damage_class: DamageClass | None = None
    location_source: LocationSource | None = None
    location_area_only: bool = False


class MapPoint(BaseModel):
    id: uuid.UUID
    damage_class: DamageClass
    map_point: LocationOut
    location_source: LocationSource | None = None
    location_area_only: bool = False
    is_anomaly: bool = False


class MapResponse(BaseModel):
    mode: str  # "clusters" | "points"
    cells: list[MapClusterCell] = []
    items: list[MapPoint] = []
    total: int
    # Uncapped match count, set only when the semantic cap was hit.
    total_match_count: int | None = None
    capped: bool = False
