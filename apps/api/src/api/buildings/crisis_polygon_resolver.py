"""Collapse crisis-area inputs into one stored MultiPolygon.

Priority: explicit geometry, else the union of the listed countries, else NULL.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shapely import wkb
from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.ops import unary_union

from api.areas import OvertureDivisionsReader
from api.schemas.crises import CrisisGeometryInput, CrisisGeometryPart


@dataclass(frozen=True)
class ResolvedGeometry:
    """wkb is None when there is no usable geometry or countries. auto_seed_countries
    holds the ISO-2 codes implied by division ids, used to fill crises.countries
    when the request omits it."""

    wkb: bytes | None
    auto_seed_countries: list[str]


class CrisisPolygonResolver:
    """Resolve crisis-area inputs into MultiPolygon WKB. Routes should call resolve()."""

    def __init__(self, reader: OvertureDivisionsReader) -> None:
        self._reader = reader

    async def from_bbox(self, bbox: tuple[float, float, float, float]) -> bytes:
        xmin, ymin, xmax, ymax = bbox
        rect = Polygon([(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax), (xmin, ymin)])
        return _to_multipolygon_wkb(rect)

    async def from_polygon(self, geojson: dict[str, Any]) -> bytes:
        return _to_multipolygon_wkb(shape(geojson))

    async def from_parts(self, parts: list[CrisisGeometryPart]) -> ResolvedGeometry:
        """Union division-id and inline-polygon parts into one MultiPolygon WKB.

        An unknown division_id raises AreaNotFoundError.
        """
        geoms: list[Any] = []
        countries: list[str] = []
        for part in parts:
            if part.division_id is not None:
                resolved = await self._reader.read_area_geometry(part.division_id)
                geoms.append(wkb.loads(resolved.wkb))
                if resolved.country is not None:
                    countries.append(resolved.country)
            elif part.polygon is not None:
                geoms.append(shape(part.polygon))
        if not geoms:  # unreachable: parts is validated non-empty
            return ResolvedGeometry(wkb=None, auto_seed_countries=[])
        seen: set[str] = set()
        seeded = [c for c in countries if not (c in seen or seen.add(c))]
        return ResolvedGeometry(
            wkb=_to_multipolygon_wkb(unary_union(geoms)),
            auto_seed_countries=seeded,
        )

    async def resolve(
        self,
        geometry: CrisisGeometryInput | None,
        countries: list[str] | None,
    ) -> ResolvedGeometry:
        """Geometry first, then the countries union, then None."""
        if geometry is not None:
            return await self._from_geometry_input(geometry)
        if countries:
            return await self._from_countries_union(countries)
        return ResolvedGeometry(wkb=None, auto_seed_countries=[])

    async def _from_geometry_input(self, geometry: CrisisGeometryInput) -> ResolvedGeometry:
        if geometry.bbox is not None:
            return ResolvedGeometry(wkb=await self.from_bbox(geometry.bbox), auto_seed_countries=[])
        if geometry.polygon is not None:
            return ResolvedGeometry(
                wkb=await self.from_polygon(geometry.polygon), auto_seed_countries=[]
            )
        if geometry.parts is not None:
            return await self.from_parts(geometry.parts)
        if geometry.division_id is not None:
            resolved = await self._reader.read_area_geometry(geometry.division_id)
            return ResolvedGeometry(
                wkb=_to_multipolygon_wkb(wkb.loads(resolved.wkb)),
                auto_seed_countries=([resolved.country] if resolved.country is not None else []),
            )
        # Unreachable: CrisisGeometryInput requires exactly one branch.
        return ResolvedGeometry(wkb=None, auto_seed_countries=[])

    async def _from_countries_union(self, countries: list[str]) -> ResolvedGeometry:
        """Union of the listed countries, or None if none matched."""
        wkb_bytes = await self._reader.read_countries_union(countries)
        if wkb_bytes is None:
            return ResolvedGeometry(wkb=None, auto_seed_countries=[])
        return ResolvedGeometry(
            wkb=_to_multipolygon_wkb(wkb.loads(wkb_bytes)),
            auto_seed_countries=[],
        )


def _to_multipolygon_wkb(geom: Any) -> bytes:
    """MultiPolygon WKB from any polygonal geometry, flattening GeometryCollections."""
    polys = list(_iter_polygons(geom))
    if not polys:
        raise ValueError(f"cannot coerce {type(geom).__name__} to MultiPolygon")
    return MultiPolygon(polys).wkb


def _iter_polygons(geom: Any) -> list[Polygon]:
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    if hasattr(geom, "geoms"):
        out: list[Polygon] = []
        for inner in geom.geoms:
            out.extend(_iter_polygons(inner))
        return out
    return []


__all__ = ["CrisisPolygonResolver", "ResolvedGeometry"]
