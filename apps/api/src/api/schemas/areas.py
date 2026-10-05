from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class AreaResponse(BaseModel):
    """Overture or OSM-fallback row; OSM rows carry their geometry inline."""

    id: str
    name: str
    subtype: str
    country: str | None
    parents: list[str]
    bbox: tuple[float, float, float, float]
    source: Literal["overture", "osm"] = "overture"
    geometry: dict[str, Any] | None = None


class CountryResponse(BaseModel):
    """Cached by the admin app at boot."""

    id: str
    iso2: str
    name: str


class AreaGeometryResponse(BaseModel):
    """GeoJSON (Multi)Polygon for previewing a picked area."""

    type: Literal["Polygon", "MultiPolygon"]
    coordinates: list[Any]
