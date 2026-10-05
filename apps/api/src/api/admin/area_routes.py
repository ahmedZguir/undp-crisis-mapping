"""Admin area lookup: Overture divisions in Postgres, with an OSM fallback."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from api.areas import (
    Area,
    AreaNotFoundError,
    Country,
    OSMArea,
    OSMAreaSearcher,
    OvertureDivisionsReader,
    PostgresOvertureDivisionsReader,
)
from api.core.app_state import AppState, get_app_state
from api.schemas.areas import AreaGeometryResponse, AreaResponse, CountryResponse

router = APIRouter(prefix="/areas")

# With the PWA's 250 ms debounce, this caps Nominatim to roughly one call per
# typed word that Overture can't answer.
OSM_FALLBACK_MIN_Q_LEN = 4


def get_overture_divisions_reader(
    state: Annotated[AppState, Depends(get_app_state)],
) -> OvertureDivisionsReader:
    return PostgresOvertureDivisionsReader(state.sessionmaker)


def get_osm_area_searcher(
    state: Annotated[AppState, Depends(get_app_state)],
) -> OSMAreaSearcher:
    return state.osm_area_searcher


@router.get("/search", response_model=list[AreaResponse])
async def search_areas(
    q: Annotated[str, Query(min_length=1, description="Substring to match against names")],
    reader: Annotated[OvertureDivisionsReader, Depends(get_overture_divisions_reader)],
    osm: Annotated[OSMAreaSearcher, Depends(get_osm_area_searcher)],
    country: Annotated[
        str | None,
        Query(min_length=2, max_length=2, description="Optional ISO-2 narrowing filter"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    source: Annotated[
        Literal["osm"] | None,
        Query(
            description=(
                "Force a single source. `osm` skips Overture entirely; used by"
                " the picker when an Overture pick had no polygon and we want"
                " OSM alternatives for the same name."
            )
        ),
    ] = None,
) -> list[AreaResponse]:
    if source == "osm":
        if len(q.strip()) < OSM_FALLBACK_MIN_Q_LEN:
            return []
        osm_hits = await osm.search(q, country, limit=min(limit, 10))
        return [_to_osm_area_response(h) for h in osm_hits]

    areas = await reader.search(q, country, limit)
    if areas:
        return [_to_area_response(a) for a in areas]
    if len(q.strip()) < OSM_FALLBACK_MIN_Q_LEN:
        return []
    osm_hits = await osm.search(q, country, limit=min(limit, 10))
    return [_to_osm_area_response(h) for h in osm_hits]


@router.get("/countries", response_model=list[CountryResponse])
async def list_countries(
    reader: Annotated[OvertureDivisionsReader, Depends(get_overture_divisions_reader)],
) -> list[CountryResponse]:
    countries = await reader.list_countries()
    return [_to_country_response(c) for c in countries]


@router.get("/countries/geometry", response_model=AreaGeometryResponse)
async def get_countries_geometry(
    reader: Annotated[OvertureDivisionsReader, Depends(get_overture_divisions_reader)],
    iso: Annotated[
        list[str],
        Query(description="ISO-2 country codes (repeatable, e.g. ?iso=QA&iso=SA)"),
    ],
) -> AreaGeometryResponse:
    """Union of the given countries as a GeoJSON MultiPolygon; 404 if nothing matches."""
    geom = await reader.read_countries_geojson_union(iso)
    if geom is None:
        raise HTTPException(status_code=404, detail="no polygons matched the given ISO-2 codes")
    return AreaGeometryResponse.model_validate(geom)


@router.get("/{division_id}/geometry", response_model=AreaGeometryResponse)
async def get_area_geometry(
    reader: Annotated[OvertureDivisionsReader, Depends(get_overture_divisions_reader)],
    division_id: Annotated[str, Path(min_length=1, description="GERS division id from search")],
) -> AreaGeometryResponse:
    """Full outline for one division; search results carry only a bbox to stay small."""
    try:
        geom = await reader.read_area_geojson(division_id)
    except AreaNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return AreaGeometryResponse.model_validate(geom)


def _to_area_response(area: Area) -> AreaResponse:
    return AreaResponse(
        id=area.id,
        name=area.name,
        subtype=area.subtype,
        country=area.country,
        parents=list(area.parents),
        bbox=area.bbox,
    )


def _to_osm_area_response(hit: OSMArea) -> AreaResponse:
    return AreaResponse(
        id=hit.osm_id,
        name=hit.name,
        subtype=hit.subtype,
        country=hit.country,
        parents=list(hit.parents),
        bbox=hit.bbox,
        source="osm",
        geometry=hit.geometry,
    )


def _to_country_response(country: Country) -> CountryResponse:
    return CountryResponse(id=country.id, iso2=country.iso2, name=country.name)
