"""SQL fragments and constants shared by the analysis queries."""

from __future__ import annotations

# `infra_type` values that count as public services; residential/commercial are
# exposure, not services.
CRITICAL_INFRA_TYPES: frozenset[str] = frozenset(
    {
        "utility",
        "transport",
        "government",
        "community",
        "public_spaces",
        "health",
        "education",
        "water",
        "power",
        "telecom",
    }
)

# A report's map position: the trigger-maintained `map_point` column (pin, then
# building centroid, then AI geocode), which avoids a per-row buildings join.
MAP_POINT = "r.map_point::geometry"

# Divisions of :subtype overlapping the crisis AOI, one unioned geometry each.
AOI_DIVISIONS_CTE = """
    aoi as (select geometry::geometry g from public.crises where id = :cid),
    divs as (
        select da.division_id, st_union(da.geometry::geometry) as geom
        from public.overture_division_areas da
        cross join aoi
        where da.subtype = :subtype and da.geometry && aoi.g
        group by da.division_id
    )"""
