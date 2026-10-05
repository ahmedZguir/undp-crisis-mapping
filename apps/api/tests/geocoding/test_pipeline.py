"""Unit tests for the pure geocoding pipeline transforms.

No DB, no HTTP, no LLM — these pin tier classification, the AOI filter,
fusion, the uncertainty-radius / pin-vs-area decision, and confidence. The
I/O orchestration is covered by the worker integration test.
"""

from __future__ import annotations

from typing import Any

from shapely.geometry import Point, box

from api.ai.types import ToponymMention
from api.geocoding import (
    GeocodeResult,
    SpatialPrior,
    classify_tier,
    first_in_aoi,
    fuse,
    in_aoi,
    row_to_geocode,
    to_output,
    uncertainty_radius_m,
)

# Antakya-ish AOI; a city box and a small nested neighborhood box.
_AOI = box(36.10, 36.15, 36.25, 36.25)
_PRIOR = SpatialPrior(aoi=_AOI, country_codes=["tr"])


def _mention(surface: str, type_hint: str = "area") -> ToponymMention:
    return ToponymMention(surface_form=surface, type_hint=type_hint)  # pyright: ignore[reportArgumentType]


def _result(surface: str, geom_box: tuple[float, float, float, float], tier: str) -> GeocodeResult:
    geom = box(*geom_box)
    return GeocodeResult(
        toponym=_mention(surface),
        geometry=geom,
        bbox=geom_box,
        tier=tier,
    )


# ---------------------------------------------------------------------------
# Tier classification
# ---------------------------------------------------------------------------


def test_classify_tier_maps_known_addresstype() -> None:
    assert classify_tier({"addresstype": "neighbourhood"}) == "neighborhood"
    assert classify_tier({"addresstype": "road"}) == "street"
    assert classify_tier({"type": "amenity"}) == "poi"


def test_classify_tier_unknown_defaults_to_city() -> None:
    assert classify_tier({"addresstype": "galaxy"}) == "city"
    assert classify_tier({}) == "city"


# ---------------------------------------------------------------------------
# Row projection — keeps node-only POIs (the relaxed-node-filter requirement)
# ---------------------------------------------------------------------------


def test_row_to_geocode_keeps_node_point() -> None:
    row: dict[str, Any] = {
        "lat": "36.20",
        "lon": "36.16",
        "addresstype": "amenity",
        "boundingbox": ["36.199", "36.201", "36.159", "36.161"],
        # no "geojson" → node fallback to point
    }
    result = row_to_geocode(row, _mention("the clinic", "landmark"))
    assert result is not None
    assert result.geometry.geom_type == "Point"
    assert result.tier == "poi"


def test_row_to_geocode_uses_polygon_when_present() -> None:
    row: dict[str, Any] = {
        "addresstype": "city",
        "boundingbox": ["36.15", "36.25", "36.10", "36.25"],
        "geojson": {
            "type": "Polygon",
            "coordinates": [[[36.10, 36.15], [36.25, 36.15], [36.25, 36.25], [36.10, 36.15]]],
        },
    }
    result = row_to_geocode(row, _mention("Antakya"))
    assert result is not None
    assert result.geometry.geom_type == "Polygon"


def test_row_to_geocode_drops_row_without_bbox() -> None:
    row: dict[str, Any] = {"lat": "1", "lon": "1", "addresstype": "amenity"}
    assert row_to_geocode(row, _mention("x")) is None


# ---------------------------------------------------------------------------
# AOI filter
# ---------------------------------------------------------------------------


def test_in_aoi_true_inside_false_outside() -> None:
    inside = _result("in", (36.16, 36.19, 36.17, 36.20), "neighborhood")
    outside = _result("out", (40.0, 40.0, 40.1, 40.1), "neighborhood")
    assert in_aoi(inside, _PRIOR) is True
    assert in_aoi(outside, _PRIOR) is False


def test_first_in_aoi_skips_out_of_area_top_hit() -> None:
    out = _result("out", (40.0, 40.0, 40.1, 40.1), "street")
    good = _result("good", (36.16, 36.19, 36.17, 36.20), "street")
    assert first_in_aoi([out, good], _PRIOR) is good
    assert first_in_aoi([out], _PRIOR) is None


def test_in_aoi_true_when_no_prior_polygon() -> None:
    prior = SpatialPrior(aoi=None, country_codes=["tr"])
    anywhere = _result("x", (40.0, 40.0, 40.1, 40.1), "city")
    assert in_aoi(anywhere, prior) is True


# ---------------------------------------------------------------------------
# Fusion
# ---------------------------------------------------------------------------


def test_fuse_single_is_identity() -> None:
    only = _result("Antakya", (36.10, 36.15, 36.25, 36.25), "city")
    assert fuse([only]) is only


def test_fuse_nested_uses_intersection_at_finest_tier() -> None:
    city = _result("Antakya", (36.10, 36.15, 36.25, 36.25), "city")
    hood = _result("Yeni Cami", (36.16, 36.19, 36.17, 36.20), "neighborhood")
    fused = fuse([city, hood])
    # Overlap is the neighborhood box (nested), tier is the finest present.
    assert fused.tier == "neighborhood"
    assert not fused.geometry.is_empty
    # The fused centroid sits inside the neighborhood, not the whole city.
    assert hood.geometry.contains(fused.geometry.centroid)


def test_fuse_disjoint_falls_back_to_finest_never_averages() -> None:
    city = _result("Antakya", (36.10, 36.15, 36.20, 36.20), "city")
    far_street = _result("Far St", (36.22, 36.22, 36.23, 36.23), "street")
    fused = fuse([city, far_street])
    # Disjoint → keep the finer one (street), do NOT average to dead space.
    assert fused.tier == "street"
    assert fused.geometry.equals(far_street.geometry)


# ---------------------------------------------------------------------------
# Pin-vs-area decision + output
# ---------------------------------------------------------------------------


def test_small_polygon_is_a_pin_large_is_area_only() -> None:
    tiny = _result("block", (36.160, 36.200, 36.161, 36.201), "building")
    big = _result("Antakya", (36.10, 36.15, 36.25, 36.25), "city")
    assert uncertainty_radius_m(tiny) < uncertainty_radius_m(big)

    tiny_out = to_output(tiny, [tiny], _PRIOR)
    big_out = to_output(big, [big], _PRIOR)
    assert tiny_out.area_only is False
    assert big_out.area_only is True
    assert big_out.polygon is not None  # polygon retained even when area-only


def test_output_carries_source_and_confidence() -> None:
    hood = _result("Yeni Cami", (36.16, 36.19, 36.17, 36.20), "neighborhood")
    out = to_output(hood, [hood], _PRIOR)
    assert out.source == "osm"
    assert 0.0 <= out.confidence <= 1.0
    assert _AOI.contains(Point(out.lon, out.lat))


def test_confidence_capped_without_country_prior() -> None:
    hood = _result("Yeni Cami", (36.16, 36.19, 36.17, 36.20), "neighborhood")
    with_country = to_output(hood, [hood], _PRIOR).confidence
    no_country = to_output(hood, [hood], SpatialPrior(aoi=_AOI, country_codes=[])).confidence
    assert no_country < with_country
