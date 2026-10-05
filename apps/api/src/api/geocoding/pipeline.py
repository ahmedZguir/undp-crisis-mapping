"""Pure geocoding transforms: tier, AOI filter, fusion, pin-vs-area, confidence.

A coarse result is flagged `area_only` so the map shades an area instead of
dropping a misleading pin on a centroid.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry

from api.ai.types import ToponymMention

# Finest to coarsest.
TIER_ORDER: tuple[str, ...] = (
    "poi",
    "building",
    "street",
    "neighborhood",
    "city",
    "county",
    "state",
    "country",
)

# Pin-vs-area radius threshold, roughly street/neighborhood scale.
DEFAULT_THRESHOLD_M = 400.0

_M_PER_DEG_LAT = 111_320.0  # ~constant; longitude scales by cos(lat)

_ADDRESSTYPE_TO_TIER: dict[str, str] = {
    "amenity": "poi",
    "shop": "poi",
    "tourism": "poi",
    "building": "building",
    "house": "building",
    "road": "street",
    "residential": "street",
    "neighbourhood": "neighborhood",
    "suburb": "neighborhood",
    "quarter": "neighborhood",
    "city": "city",
    "town": "city",
    "village": "city",
    "county": "county",
    "state": "state",
    "province": "state",
    "country": "country",
}


@dataclass(frozen=True)
class SpatialPrior:
    """Crisis area used to constrain lookups.

    `aoi` is a WGS84 polygon or None. `country_codes` are lowercase ISO 3166
    alpha-2; when empty, confidence is halved.
    """

    aoi: BaseGeometry | None
    country_codes: list[str]


@dataclass
class GeocodeResult:
    toponym: ToponymMention
    geometry: BaseGeometry  # Polygon/MultiPolygon for areas, Point for node POIs
    bbox: tuple[float, float, float, float]  # (xmin, ymin, xmax, ymax)
    tier: str


@dataclass
class ResolvedLocation:
    lat: float
    lon: float
    granularity_tier: str
    radius_m: float
    confidence: float
    area_only: bool  # render as a shaded area, not a pin
    source: str = "osm"
    polygon: dict[str, Any] | None = None  # GeoJSON, when available
    toponyms: list[GeocodeResult] = field(default_factory=list[GeocodeResult])


def classify_tier(row: Mapping[str, Any]) -> str:
    """Unknown types default to 'city' so they never pass as precise."""
    addresstype = row.get("addresstype") or row.get("type") or ""
    if not isinstance(addresstype, str):
        return "city"
    return _ADDRESSTYPE_TO_TIER.get(addresstype, "city")


def parse_bbox(raw: Any) -> tuple[float, float, float, float] | None:
    """Convert Nominatim's `[ymin, ymax, xmin, xmax]` strings to `(xmin, ymin, xmax, ymax)`."""
    if not isinstance(raw, list):
        return None
    items = cast(list[Any], raw)
    if len(items) != 4:
        return None
    try:
        ymin, ymax, xmin, xmax = (float(v) for v in items)
    except (TypeError, ValueError):
        return None
    return (xmin, ymin, xmax, ymax)


def row_to_geocode(row: Mapping[str, Any], toponym: ToponymMention) -> GeocodeResult | None:
    """Nodes without a polygon become points. Returns None when the row has no bbox."""
    geojson = row.get("geojson")
    geometry: BaseGeometry
    if isinstance(geojson, dict):
        geometry = shape(cast(dict[str, Any], geojson))
    else:
        lon, lat = row.get("lon"), row.get("lat")
        if lon is None or lat is None:
            return None
        try:
            geometry = Point(float(lon), float(lat))
        except (TypeError, ValueError):
            return None
    bbox = parse_bbox(row.get("boundingbox"))
    if bbox is None:
        return None
    return GeocodeResult(
        toponym=toponym,
        geometry=geometry,
        bbox=bbox,
        tier=classify_tier(row),
    )


def in_aoi(result: GeocodeResult, prior: SpatialPrior) -> bool:
    if prior.aoi is None:
        return True
    return prior.aoi.intersects(result.geometry)


def first_in_aoi(candidates: list[GeocodeResult], prior: SpatialPrior) -> GeocodeResult | None:
    return next((c for c in candidates if in_aoi(c, prior)), None)


def _finest(results: list[GeocodeResult]) -> GeocodeResult:
    return min(results, key=lambda r: TIER_ORDER.index(r.tier))


def fuse(results: list[GeocodeResult]) -> GeocodeResult:
    """Intersect all places at the finest tier present, or take the finest if they don't overlap.

    Disjoint points are never averaged; the mean lands somewhere nobody named.
    """
    if len(results) == 1:
        return results[0]

    overlap = results[0].geometry
    for r in results[1:]:
        overlap = overlap.intersection(r.geometry)

    finest = _finest(results)
    if overlap.is_empty:
        return finest
    return GeocodeResult(
        toponym=finest.toponym,
        geometry=overlap,
        bbox=overlap.bounds,
        tier=finest.tier,
    )


def _bbox_dims_m(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    """(width, height) in metres, equirectangular approximation."""
    xmin, ymin, xmax, ymax = bbox
    lat_mid = math.radians((ymin + ymax) / 2)
    width = (xmax - xmin) * _M_PER_DEG_LAT * math.cos(lat_mid)
    height = (ymax - ymin) * _M_PER_DEG_LAT
    return abs(width), abs(height)


def uncertainty_radius_m(result: GeocodeResult) -> float:
    """Equivalent-circle radius for polygons, bbox half-diagonal for points.

    Nominatim returns a bbox even for nodes, so a city node still reads as coarse.
    """
    if result.geometry.geom_type in ("Polygon", "MultiPolygon"):
        w, h = _bbox_dims_m(result.geometry.bounds)
        envelope_area = result.geometry.envelope.area or 1.0
        area = w * h * (result.geometry.area / envelope_area)
        return math.sqrt(max(area, 0.0) / math.pi)
    w, h = _bbox_dims_m(result.bbox)
    return math.hypot(w, h) / 2


def is_granular_enough(radius_m: float, threshold_m: float = DEFAULT_THRESHOLD_M) -> bool:
    return radius_m <= threshold_m


def compute_confidence(fused: GeocodeResult, prior: SpatialPrior) -> float:
    """Tier granularity * AOI fit, halved without a country prior."""
    tier = 1.0 - TIER_ORDER.index(fused.tier) / (len(TIER_ORDER) - 1)  # finer -> higher
    inside_aoi = prior.aoi is not None and prior.aoi.contains(fused.geometry.centroid)
    aoi_fit = 1.0 if inside_aoi else 0.6
    base = tier * aoi_fit
    return round(base * (0.5 if not prior.country_codes else 1.0), 3)


def to_output(
    fused: GeocodeResult,
    all_results: list[GeocodeResult],
    prior: SpatialPrior,
    threshold_m: float = DEFAULT_THRESHOLD_M,
) -> ResolvedLocation:
    centroid = fused.geometry.centroid
    radius = uncertainty_radius_m(fused)
    polygon: dict[str, Any] | None = None
    if fused.geometry.geom_type in ("Polygon", "MultiPolygon"):
        polygon = dict(fused.geometry.__geo_interface__)
    return ResolvedLocation(
        lat=centroid.y,
        lon=centroid.x,
        granularity_tier=fused.tier,
        radius_m=round(radius, 1),
        confidence=compute_confidence(fused, prior),
        area_only=not is_granular_enough(radius, threshold_m),
        polygon=polygon,
        toponyms=all_results,
    )


def toponym_audit(results: list[GeocodeResult]) -> list[dict[str, Any]]:
    """Per-mention JSON for the `toponyms` column; stores centroids, not geometries."""
    audit: list[dict[str, Any]] = []
    for r in results:
        centroid = r.geometry.centroid
        audit.append(
            {
                "surface_form": r.toponym.surface_form,
                "corrected_form": r.toponym.corrected_form,
                "type_hint": r.toponym.type_hint,
                "tier": r.tier,
                "lat": centroid.y,
                "lon": centroid.x,
            }
        )
    return audit
