"""Shapes for /admin/reports/search. Filters are optional and combine with AND."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from api.schemas.common import DamageClass, LocationOut

Strictness = Literal["loose", "balanced", "strict"]
DebrisFilter = Literal["yes", "no", "any"]
# "exact" means GPS pin or matched building; "ai_geocode" means placed only by the geocode.
LocationKindFilter = Literal["exact", "ai_geocode", "any"]
DamageClassFilter = Literal["minimal", "partial", "complete", "any"]
# Source of map_point, in precedence order.
LocationSource = Literal["submitted_pin", "building_centroid", "ai_geocode"]


class TimeWindow(BaseModel):
    """Both ends optional. Naive datetimes are read in the DB session timezone."""

    from_: datetime | None = Field(default=None, alias="from")
    to: datetime | None = None

    model_config = {"populate_by_name": True}


class LocationFilter(BaseModel):
    """Division ids, a drawn GeoJSON polygon and/or a viewport bbox, unioned."""

    division_ids: list[str] | None = None
    polygon: dict[str, Any] | None = None
    bbox: tuple[float, float, float, float] | None = None


class SearchRequest(BaseModel):
    time_window: TimeWindow | None = None
    location: LocationFilter | None = None
    query: str | None = None
    strictness: Strictness = "balanced"
    infra_types: list[str] | None = None
    debris: DebrisFilter = "any"
    location_kind: LocationKindFilter = "any"
    damage_class: DamageClassFilter = "any"
    building_id: uuid.UUID | None = None
    limit: int = Field(default=2000, ge=1, le=20000)


class SearchHit(BaseModel):
    """Subset of the admin list row."""

    id: uuid.UUID
    damage_class: DamageClass
    description: str | None
    description_en: str | None = None
    infra_type: list[str] | None
    infra_name: str | None
    route_description: str | None = None
    debris: bool | None
    building_id: uuid.UUID | None
    building_name: str | None = None
    location: LocationOut | None
    map_point: LocationOut | None
    # The radius/area/confidence fields are set only for ai_geocode points.
    location_source: LocationSource | None = None
    location_radius_m: float | None = None
    location_area_only: bool = False  # too coarse to pin; render as an area
    location_confidence: float | None = None
    photo_path: str | None
    created_at: datetime
    # Cosine similarity; set only when the request has a query.
    similarity: float | None = None
    # Similarity more than two stdevs below the result mean; always False without a query.
    is_anomaly: bool = False


class DailyBucket(BaseModel):
    """Report counts for one UTC day (YYYY-MM-DD). Days with no reports are omitted."""

    date: str
    complete: int
    partial: int
    minimal: int


class SearchStats(BaseModel):
    """Aggregates over the full filtered set, unaffected by the row limit.

    Feeds the dashboard and the chat system prompt.
    """

    total: int
    severity: dict[str, int]
    last_24h: int
    last_hour: int
    top_infra: str | None
    top_infra_count: int
    debris_yes: int
    debris_known: int
    with_building: int
    with_gps: int
    # Positioned only by the AI geocode.
    with_geocode: int = 0
    # No position at all, so never on the map or in bbox-filtered counts.
    unmapped: int = 0
    # A multi-type report counts once per type, so the sum can exceed total.
    infra_breakdown: dict[str, int] = {}
    daily: list[DailyBucket] = []
    # Distinct client ids: a lower bound, since users can reset theirs.
    unique_devices: int = 0
    # buildings_total follows the location filter, else the crisis ingest count.
    buildings_affected: int = 0
    buildings_total: int = 0


class SearchResponse(BaseModel):
    rows: list[SearchHit]
    truncated: bool
    total_match_count: int
    # Similarity cutoff applied when a query was given.
    similarity_floor: float | None = None
    # Echoed so chat and summary can key caches on the same filter.
    filter_signature: str
    stats: SearchStats | None = None


# Autocomplete for building_id and LocationFilter.division_ids


class BuildingSearchHit(BaseModel):
    id: uuid.UUID
    name: str
    # From the most recent report; None when the building has none.
    infra_type: list[str] | None
    centroid: LocationOut
    # 0 for buildings found through the crisis-bbox fallback.
    n_reports: int


class DivisionSearchHit(BaseModel):
    id: str
    name: str
    # Overture subtype: country, region, locality, ...
    admin_level: str
    country_code: str | None
    centroid: LocationOut


class ChatOpenRequest(BaseModel):
    filter: SearchRequest
    message: str


class ChatTurnRequest(BaseModel):
    session_id: str
    message: str


class ChatContextReport(BaseModel):
    """A report in the chat's K-set, for the dashboard map overlay."""

    id: uuid.UUID
    lat: float
    lng: float
    damage_class: str | None = None


class ChatTurnResponse(BaseModel):
    session_id: str
    text: str
    cited_report_ids: list[uuid.UUID]
    # Open response only.
    k_size: int | None = None
    # Open response only; empty in stats-only mode.
    context_reports: list[ChatContextReport] = []
    filter_signature: str | None = None
    # Match count at session open; the UI compares it with the live total to
    # flag a stale scope.
    opened_total: int | None = None
    answered_at: datetime | None = None
    # Open response only.
    stats: SearchStats | None = None
