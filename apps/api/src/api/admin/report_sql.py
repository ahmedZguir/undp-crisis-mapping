"""SQL fragments and row helpers shared by the admin report and search queries."""

from __future__ import annotations

from typing import Any

# Position precedence: GPS pin, then building centroid, then AI geocode. Must
# match export_queries.py. Queries using these need GEOCODE_JOIN. Geo predicates
# use the indexed reports.map_point.
LOCATION_SOURCE_SQL = (
    "(case "
    "when r.location is not null then 'submitted_pin' "
    "when b.centroid is not null then 'building_centroid' "
    "when g.lat is not null and g.lon is not null then 'ai_geocode' "
    "else null end)"
)
# Trailing comma: spliced before the next select column.
GEOCODE_PROJECTION = """
        g.lat as geocode_lat,
        g.lon as geocode_lon,
        g.radius_m as geocode_radius_m,
        g.area_only as geocode_area_only,
        g.confidence as geocode_confidence,
"""
GEOCODE_JOIN = "left join public.report_geocodes g on g.report_id = r.id"


def float_or_none(value: Any) -> float | None:
    # report_geocodes.confidence arrives as Decimal, so do not stop at int|float.
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def geocode_meta(row: Any, source: str | None) -> tuple[float | None, bool, float | None]:
    """(radius_m, area_only, confidence), populated only for an ai_geocode point."""
    if source != "ai_geocode":
        return None, False, None
    radius_m = float_or_none(getattr(row, "geocode_radius_m", None))
    area_only = bool(getattr(row, "geocode_area_only", None))
    confidence = float_or_none(getattr(row, "geocode_confidence", None))
    return radius_m, area_only, confidence
