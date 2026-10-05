"""Admin damaged-buildings layer: footprints with report stats, as GeoJSON."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from api.schemas.admin_search import SearchRequest
from api.schemas.common import DamageClass


class BuildingStatsRequest(SearchRequest):
    """limit counts buildings, not reports."""

    bbox: tuple[float, float, float, float]
    limit: int = Field(default=500, ge=1, le=10000)


class PolygonGeometry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["Polygon"] = "Polygon"
    coordinates: list[list[list[float]]]


class MultiPolygonGeometry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["MultiPolygon"] = "MultiPolygon"
    coordinates: list[list[list[list[float]]]]


class AdminBuildingProperties(BaseModel):
    """latest_damage_class comes from the building's most recent report."""

    model_config = ConfigDict(extra="forbid")

    building_id: str
    name: str | None
    report_count: int
    latest_damage_class: DamageClass
    latest_at: datetime
    minimal_count: int
    partial_count: int
    complete_count: int
    centroid_lat: float
    centroid_lng: float


class AdminBuildingFeature(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["Feature"] = "Feature"
    geometry: PolygonGeometry | MultiPolygonGeometry
    properties: AdminBuildingProperties


class AdminBuildingsFeatureCollection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[AdminBuildingFeature]
    # The query hit limit.
    truncated: bool = False
