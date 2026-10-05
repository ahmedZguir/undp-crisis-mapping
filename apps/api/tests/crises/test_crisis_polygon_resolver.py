"""Unit tests for `CrisisPolygonResolver`.

The resolver collapses every supported geometry-input form (`bbox`, `polygon`,
`division_id`) into a single stored shape — a WKB MultiPolygon. The
`division_id` branch goes through the `OvertureDivisionsReader` Protocol seam
so this suite never touches live S3.

`resolve()` is the orchestration entry point: it implements the priority
rule `geometry → countries-union → null`. Routes call only
`resolve()`; per-branch helpers (`from_bbox`, `from_polygon`) are kept public
for direct use in narrow tests but are not the route's path.
"""

from __future__ import annotations

from shapely import wkb
from shapely.geometry import MultiPolygon, Polygon, mapping

from api.areas import AreaNotFoundError, OvertureDivisionsReader, ResolvedArea
from api.areas.overture_divisions_reader import Area, Country
from api.buildings.crisis_polygon_resolver import CrisisPolygonResolver
from api.schemas.crises import CrisisGeometryInput, CrisisGeometryPart

_TOL = 1e-9


def _approx(a: float, b: float, tol: float = _TOL) -> bool:
    return abs(a - b) <= tol


# --- from_bbox / from_polygon (no reader I/O) ------------------------------


async def test_from_bbox_produces_5_vertex_rectangular_multipolygon() -> None:
    resolver = CrisisPolygonResolver(reader=_NeverCalledReader())

    out_wkb = await resolver.from_bbox((10.0, 20.0, 11.0, 21.0))

    geom = wkb.loads(out_wkb)
    assert isinstance(geom, MultiPolygon)
    polys = list(geom.geoms)
    assert len(polys) == 1
    coords = list(polys[0].exterior.coords)
    # Closed ring of a rectangle: 5 vertices (first == last).
    assert len(coords) == 5
    assert coords[0] == coords[-1]
    xs, ys = [c[0] for c in coords], [c[1] for c in coords]
    assert _approx(min(xs), 10.0) and _approx(max(xs), 11.0)
    assert _approx(min(ys), 20.0) and _approx(max(ys), 21.0)


async def test_from_polygon_round_trips_geojson_polygon() -> None:
    resolver = CrisisPolygonResolver(reader=_NeverCalledReader())
    poly = Polygon([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)])

    out_wkb = await resolver.from_polygon(dict(mapping(poly)))

    geom = wkb.loads(out_wkb)
    assert isinstance(geom, MultiPolygon)
    assert _approx(geom.area, 1.0)
    bounds = geom.bounds
    assert _approx(bounds[0], 0.0) and _approx(bounds[1], 0.0)
    assert _approx(bounds[2], 1.0) and _approx(bounds[3], 1.0)


async def test_from_polygon_accepts_geojson_multipolygon_directly() -> None:
    resolver = CrisisPolygonResolver(reader=_NeverCalledReader())
    multi = MultiPolygon(
        [
            Polygon([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)]),
            Polygon([(2.0, 2.0), (3.0, 2.0), (3.0, 3.0), (2.0, 2.0)]),
        ]
    )

    out_wkb = await resolver.from_polygon(dict(mapping(multi)))

    geom = wkb.loads(out_wkb)
    assert isinstance(geom, MultiPolygon)
    assert len(list(geom.geoms)) == 2


# --- division_id (reader-backed) -------------------------------------------


async def test_division_id_returns_normalized_multipolygon_wkb() -> None:
    """A polygon-only fixture (not yet a multi) must be normalised to
    MultiPolygon before being handed back."""
    poly = Polygon([(50.7, 24.4), (51.7, 24.4), (51.7, 26.2), (50.7, 26.2), (50.7, 24.4)])
    reader = _StaticReader({"qa": ResolvedArea(wkb=poly.wkb, country="QA")})

    resolver = CrisisPolygonResolver(reader=reader)

    resolved = await resolver.resolve(
        geometry=CrisisGeometryInput(division_id="qa"), countries=None
    )

    assert resolved.wkb is not None
    geom = wkb.loads(resolved.wkb)
    assert isinstance(geom, MultiPolygon)
    assert geom.area > 0


async def test_division_id_propagates_area_not_found() -> None:
    """An unknown id raises `AreaNotFoundError` — the route turns this into
    a 400 rather than letting it cascade as 500."""
    reader = _StaticReader({})

    resolver = CrisisPolygonResolver(reader=reader)

    try:
        await resolver.resolve(geometry=CrisisGeometryInput(division_id="missing"), countries=None)
    except AreaNotFoundError:
        return
    raise AssertionError("expected AreaNotFoundError")


# --- resolve() priority rule -----------------------------------------------


async def test_resolve_with_no_geometry_and_empty_countries_returns_none() -> None:
    """Priority-3 fall-through: nothing to resolve -> None (caller writes
    `crises.geometry = NULL`)."""
    resolver = CrisisPolygonResolver(reader=_NeverCalledReader())

    resolved = await resolver.resolve(geometry=None, countries=[])

    assert resolved.wkb is None
    assert resolved.auto_seed_countries == []


async def test_resolve_with_division_id_uses_geometry_branch_and_seeds_country() -> None:
    """Priority-1: `geometry` is set -> resolve via the geometry branch.
    The picked area's country travels back as the auto-seed value."""
    poly = Polygon([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)])
    reader = _StaticReader({"iq-baghdad": ResolvedArea(wkb=poly.wkb, country="IQ")})

    resolver = CrisisPolygonResolver(reader=reader)
    resolved = await resolver.resolve(
        geometry=CrisisGeometryInput(division_id="iq-baghdad"),
        countries=None,
    )

    assert resolved.wkb is not None
    geom = wkb.loads(resolved.wkb)
    assert isinstance(geom, MultiPolygon)
    assert resolved.auto_seed_countries == ["IQ"]


async def test_resolve_with_bbox_does_not_seed_country() -> None:
    """`bbox` carries no country signal — auto_seed_countries stays empty."""
    resolver = CrisisPolygonResolver(reader=_NeverCalledReader())

    resolved = await resolver.resolve(
        geometry=CrisisGeometryInput(bbox=(10.0, 20.0, 11.0, 21.0)),
        countries=None,
    )

    assert resolved.wkb is not None
    assert resolved.auto_seed_countries == []


async def test_resolve_priority_geometry_wins_over_countries_fallback() -> None:
    """Priority-1 beats priority-2: if `geometry` is set, we do not fall
    through to the countries-union branch (which is a stub in this slice
    anyway, but the priority rule still applies)."""
    poly = Polygon([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)])
    reader = _StaticReader({"a": ResolvedArea(wkb=poly.wkb, country="QA")})

    resolver = CrisisPolygonResolver(reader=reader)
    resolved = await resolver.resolve(
        geometry=CrisisGeometryInput(division_id="a"),
        countries=["IQ", "SY"],
    )

    # Geometry branch was used (we got bytes back), and the priority-2 stub
    # was never consulted (the static reader has no union method wired up).
    assert resolved.wkb is not None
    assert resolved.auto_seed_countries == ["QA"]


async def test_resolve_countries_only_calls_read_countries_union() -> None:
    """Priority-2: no geometry, but `countries` set -> resolver returns the
    polygon-union via the reader. `auto_seed_countries` stays empty (caller
    already supplied `countries` explicitly)."""
    poly = MultiPolygon(
        [
            Polygon([(0, 0), (3, 0), (3, 3), (0, 3), (0, 0)]),
            Polygon([(7, 7), (10, 7), (10, 10), (7, 10), (7, 7)]),
        ]
    )

    class _UnionReader:
        def __init__(self) -> None:
            self.union_calls: list[list[str]] = []

        async def search(self, q: str, country: str | None, limit: int) -> list[Area]:
            raise AssertionError("search should not be consulted on this branch")

        async def list_countries(self) -> list[Country]:
            raise AssertionError("list_countries should not be consulted on this branch")

        async def read_area_geometry(self, area_id: str) -> ResolvedArea:
            raise AssertionError("read_area_geometry should not be consulted on this branch")

        async def read_area_geojson(self, area_id: str) -> dict[str, object]:
            raise AssertionError("read_area_geojson should not be consulted on this branch")

        async def read_countries_union(self, iso2_codes: list[str]) -> bytes | None:
            self.union_calls.append(list(iso2_codes))
            return poly.wkb

        async def read_countries_geojson_union(
            self, iso2_codes: list[str]
        ) -> dict[str, object] | None:
            raise AssertionError("read_countries_geojson_union must not be called from these tests")

    reader = _UnionReader()
    resolver = CrisisPolygonResolver(reader=reader)

    resolved = await resolver.resolve(geometry=None, countries=["IQ", "SY"])

    assert reader.union_calls == [["IQ", "SY"]]
    assert resolved.wkb is not None
    geom = wkb.loads(resolved.wkb)
    assert isinstance(geom, MultiPolygon)
    assert resolved.auto_seed_countries == []


async def test_resolve_countries_only_falls_through_when_reader_returns_none() -> None:
    """Reader returns `None` (no matching countries) -> resolver returns
    `wkb=None` rather than raising, so the route writes `geometry = NULL`."""

    class _MissReader:
        async def search(self, q: str, country: str | None, limit: int) -> list[Area]:
            raise AssertionError("unused")

        async def list_countries(self) -> list[Country]:
            raise AssertionError("unused")

        async def read_area_geometry(self, area_id: str) -> ResolvedArea:
            raise AssertionError("unused")

        async def read_area_geojson(self, area_id: str) -> dict[str, object]:
            raise AssertionError("unused")

        async def read_countries_union(self, iso2_codes: list[str]) -> bytes | None:
            return None

        async def read_countries_geojson_union(
            self, iso2_codes: list[str]
        ) -> dict[str, object] | None:
            raise AssertionError("unused")

    resolver = CrisisPolygonResolver(reader=_MissReader())

    resolved = await resolver.resolve(geometry=None, countries=["XX"])

    assert resolved.wkb is None
    assert resolved.auto_seed_countries == []


# --- from_parts (multi-place union) ----------------------------------------


async def test_from_parts_unions_division_and_inline_polygon() -> None:
    """A division-id part and an inline-polygon part union into one
    MultiPolygon; the division's country seeds, the polygon contributes none."""
    qa = Polygon([(50.7, 24.4), (51.7, 24.4), (51.7, 26.2), (50.7, 26.2), (50.7, 24.4)])
    reader = _StaticReader({"qa": ResolvedArea(wkb=qa.wkb, country="QA")})
    inline = Polygon([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)])

    resolver = CrisisPolygonResolver(reader=reader)
    resolved = await resolver.resolve(
        geometry=CrisisGeometryInput(
            parts=[
                CrisisGeometryPart(division_id="qa"),
                CrisisGeometryPart(polygon=dict(mapping(inline))),
            ]
        ),
        countries=None,
    )

    assert resolved.wkb is not None
    geom = wkb.loads(resolved.wkb)
    assert isinstance(geom, MultiPolygon)
    # Two disjoint shapes -> two polygons in the union.
    assert len(list(geom.geoms)) == 2
    assert resolved.auto_seed_countries == ["QA"]


async def test_from_parts_dedupes_seed_countries_preserving_order() -> None:
    """Two divisions in the same country seed that country once; distinct
    countries keep first-seen order."""
    a = Polygon([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)])
    b = Polygon([(5.0, 5.0), (6.0, 5.0), (6.0, 6.0), (5.0, 5.0)])
    c = Polygon([(9.0, 9.0), (10.0, 9.0), (10.0, 10.0), (9.0, 9.0)])
    reader = _StaticReader(
        {
            "sy-aleppo": ResolvedArea(wkb=a.wkb, country="SY"),
            "sy-homs": ResolvedArea(wkb=b.wkb, country="SY"),
            "iq-mosul": ResolvedArea(wkb=c.wkb, country="IQ"),
        }
    )

    resolver = CrisisPolygonResolver(reader=reader)
    resolved = await resolver.resolve(
        geometry=CrisisGeometryInput(
            parts=[
                CrisisGeometryPart(division_id="sy-aleppo"),
                CrisisGeometryPart(division_id="sy-homs"),
                CrisisGeometryPart(division_id="iq-mosul"),
            ]
        ),
        countries=None,
    )

    assert resolved.auto_seed_countries == ["SY", "IQ"]


async def test_from_parts_overlapping_polygons_merge_into_one() -> None:
    """Overlapping parts dissolve into a single polygon (real union, not a
    bbox sweep) — the same guarantee the countries-union path gives."""
    left = Polygon([(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0), (0.0, 0.0)])
    right = Polygon([(1.0, 0.0), (3.0, 0.0), (3.0, 2.0), (1.0, 2.0), (1.0, 0.0)])

    resolver = CrisisPolygonResolver(reader=_NeverCalledReader())
    resolved = await resolver.from_parts(
        [
            CrisisGeometryPart(polygon=dict(mapping(left))),
            CrisisGeometryPart(polygon=dict(mapping(right))),
        ]
    )

    geom = wkb.loads(resolved.wkb) if resolved.wkb else None
    assert isinstance(geom, MultiPolygon)
    assert len(list(geom.geoms)) == 1
    assert resolved.auto_seed_countries == []


async def test_from_parts_unknown_division_propagates_area_not_found() -> None:
    """An unknown division id in a part raises like the single-pick branch,
    so the route returns a 400 instead of silently dropping the place."""
    resolver = CrisisPolygonResolver(reader=_StaticReader({}))

    try:
        await resolver.from_parts([CrisisGeometryPart(division_id="missing")])
    except AreaNotFoundError:
        return
    raise AssertionError("expected AreaNotFoundError")


# --- Helpers ---------------------------------------------------------------


class _StaticReader:
    """In-memory `OvertureDivisionsReader` — search/list_countries are
    asserted-unused; only `read_area_geometry` is implemented."""

    def __init__(self, areas: dict[str, ResolvedArea]) -> None:
        self._areas = areas

    async def search(self, q: str, country: str | None, limit: int) -> list[Area]:
        raise AssertionError("search must not be called from the resolver")

    async def list_countries(self) -> list[Country]:
        raise AssertionError("list_countries must not be called from the resolver")

    async def read_area_geometry(self, area_id: str) -> ResolvedArea:
        if area_id not in self._areas:
            raise AreaNotFoundError(area_id)
        return self._areas[area_id]

    async def read_area_geojson(self, area_id: str) -> dict[str, object]:
        raise AssertionError("read_area_geojson must not be called from these tests")

    async def read_countries_union(self, iso2_codes: list[str]) -> bytes | None:
        raise AssertionError("read_countries_union must not be called from these tests")

    async def read_countries_geojson_union(self, iso2_codes: list[str]) -> dict[str, object] | None:
        raise AssertionError("read_countries_geojson_union must not be called from these tests")


class _NeverCalledReader:
    async def search(self, q: str, country: str | None, limit: int) -> list[Area]:
        raise AssertionError("reader should not be called for this branch")

    async def list_countries(self) -> list[Country]:
        raise AssertionError("reader should not be called for this branch")

    async def read_area_geometry(self, area_id: str) -> ResolvedArea:
        raise AssertionError("reader should not be called for this branch")

    async def read_area_geojson(self, area_id: str) -> dict[str, object]:
        raise AssertionError("reader should not be called for this branch")

    async def read_countries_union(self, iso2_codes: list[str]) -> bytes | None:
        raise AssertionError("reader should not be called for this branch")

    async def read_countries_geojson_union(self, iso2_codes: list[str]) -> dict[str, object] | None:
        raise AssertionError("reader should not be called for this branch")


# Static type-check: both fakes satisfy the Protocol.
_: OvertureDivisionsReader = _NeverCalledReader()
_unused: OvertureDivisionsReader = _StaticReader({})
