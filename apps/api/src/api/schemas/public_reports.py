"""Public read schemas: stats, buildings-mode GeoJSON and full-mode reports.

These omit route_description, client ids and EXIF data. extra="forbid" makes a
leaked coordinator field fail validation.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from api.schemas.common import DamageClass, Debris, LocationOut


class CrisisStatsResponse(BaseModel):
    """Active crises only; others return 404."""

    total_reports: int
    by_damage_class: dict[DamageClass, int]
    last_24h: int
    last_7d: int
    affected_cells: int
    latest_at: datetime | None


# Buildings mode: one pin per damaged building, or per report with no matched
# building, carrying the worst damage class in the group.


class PointGeometry(BaseModel):
    """GeoJSON Point, [lng, lat]."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["Point"] = "Point"
    coordinates: tuple[float, float]


class BuildingFeatureProperties(BaseModel):
    """building_id is None for pins without a matched building; the PWA does not show it."""

    model_config = ConfigDict(extra="forbid")

    damage_class: DamageClass
    report_count: int
    building_id: str | None = None


class BuildingFeature(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["Feature"] = "Feature"
    geometry: PointGeometry
    properties: BuildingFeatureProperties


class BuildingsFeatureCollection(BaseModel):
    """Empty crises return an empty collection, not 204."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[BuildingFeature]


# Full mode. Rows with public_visible = false are filtered in the query.


class _PublicReportFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    crisis_id: uuid.UUID
    damage_class: DamageClass
    description: str | None
    infra_type: list[str] | None
    infra_name: str | None
    crisis_type: str | None
    crisis_type_detailed: str | None
    debris: Debris | None
    building_id: uuid.UUID | None
    location: LocationOut | None


class PublicReportListItem(_PublicReportFields):
    # map_point is coalesce(location, building centroid).
    map_point: LocationOut | None
    created_at: datetime


class PublicReportListResponse(BaseModel):
    """Cursor mode pages with next_cursor; bbox mode sets truncated and total_in_bbox."""

    model_config = ConfigDict(extra="forbid")

    items: list[PublicReportListItem]
    next_cursor: str | None = None
    truncated: bool = False
    total_in_bbox: int | None = None


class PublicReportDetail(_PublicReportFields):
    """photo_url is signed per request and short-lived; clients must not persist it."""

    building_centroid: LocationOut | None
    photo_url: str | None
    created_at: datetime
