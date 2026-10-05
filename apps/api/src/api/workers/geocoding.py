"""Geocode a report's free-text location against its crisis area.

Runs from the enrichment fan-out but is outside the finalize gate. 'ready' with
NULL lat/lon means the text named no place that resolved inside the AOI.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.ai import AIClientUnavailableError, ToponymMention, extract_toponyms
from api.ai.llm import LLMOutputError
from api.geocoding import (
    GeocodeResult,
    ResolvedLocation,
    SpatialPrior,
    first_in_aoi,
    fuse,
    row_to_geocode,
    to_output,
    toponym_audit,
)
from api.workers.context import ai_clients_from_ctx, nominatim_from_ctx, sessionmaker_from_ctx
from api.workers.errors import format_error, is_last_try
from api.workers.sidecars import execute_write, mark_failed, mark_skipped

logger = logging.getLogger(__name__)

# More than one so the AOI polygon filter has alternatives when the top hit is
# in a bbox corner outside the polygon.
_CANDIDATES_PER_MENTION = 5


async def geocode_report(ctx: dict[str, object], report_id: str) -> None:
    rid = uuid.UUID(report_id)
    sessionmaker = sessionmaker_from_ctx(ctx)
    clients = ai_clients_from_ctx(ctx)
    nominatim = nominatim_from_ctx(ctx)

    snapshot = await _load_input(sessionmaker, rid)
    if snapshot is None:
        logger.warning("geocode_report.report_missing report_id=%s", rid)
        return

    route = (snapshot.route_description or "").strip()
    if not route:
        await _mark_skipped(sessionmaker, rid)
        return

    try:
        mentions = await extract_toponyms(route, clients=clients)
    except AIClientUnavailableError as exc:
        await _write_failed(sessionmaker, rid, str(exc))
        return
    except LLMOutputError as exc:
        await _write_failed(sessionmaker, rid, f"parse_error: {exc}")
        return
    except Exception as exc:
        if is_last_try(ctx):
            await _write_failed(sessionmaker, rid, format_error(exc))
        raise

    prior = snapshot.prior()
    if not mentions:
        await _write_unresolved(sessionmaker, rid, mentions)
        return

    resolved: list[GeocodeResult] = []
    viewbox = prior.aoi.bounds if prior.aoi is not None else None
    for mention in mentions:
        query = mention.corrected_form or mention.surface_form
        rows = await nominatim.geocode_search(
            query,
            country_codes=prior.country_codes,
            viewbox=viewbox,
            limit=_CANDIDATES_PER_MENTION,
        )
        candidates = [g for row in rows if (g := row_to_geocode(row, mention)) is not None]
        hit = first_in_aoi(candidates, prior)
        if hit is not None:
            resolved.append(hit)

    if not resolved:
        await _write_unresolved(sessionmaker, rid, mentions)
        return

    output = to_output(fuse(resolved), resolved, prior)
    await _write_ready(sessionmaker, rid, output)


@dataclass(frozen=True, slots=True)
class _Input:
    route_description: str | None
    aoi_geojson: str | None
    countries: list[str]

    def prior(self) -> SpatialPrior:
        aoi: BaseGeometry | None = None
        if self.aoi_geojson:
            aoi = shape(json.loads(self.aoi_geojson))
        return SpatialPrior(
            aoi=aoi,
            country_codes=[c.lower() for c in self.countries if c],
        )


_INPUT_SQL = text(
    """
    select
        r.route_description,
        st_asgeojson(c.geometry) as aoi_geojson,
        c.countries
    from public.reports r
    join public.crises c on c.id = r.crisis_id
    where r.id = :id
    """
)


async def _load_input(
    sessionmaker: async_sessionmaker[AsyncSession], report_id: uuid.UUID
) -> _Input | None:
    async with sessionmaker() as session:
        row = (await session.execute(_INPUT_SQL, {"id": str(report_id)})).first()
    if row is None:
        return None
    countries = list(row.countries) if row.countries is not None else []
    return _Input(
        route_description=row.route_description,
        aoi_geojson=row.aoi_geojson,
        countries=countries,
    )


async def _mark_skipped(
    sessionmaker: async_sessionmaker[AsyncSession], report_id: uuid.UUID
) -> None:
    await mark_skipped(sessionmaker, report_id, table="report_geocodes")


async def _write_unresolved(
    sessionmaker: async_sessionmaker[AsyncSession],
    report_id: uuid.UUID,
    mentions: list[ToponymMention],
) -> None:
    """Mark 'ready' with no geometry, keeping the extracted mentions for coordinators."""
    await execute_write(
        sessionmaker,
        "update public.report_geocodes "
        "   set lat = null, lon = null, polygon = null, "
        "       granularity_tier = null, radius_m = null, area_only = null, "
        "       confidence = null, source = 'osm', "
        "       toponyms = cast(:toponyms as jsonb), "
        "       status = 'ready', error = null, updated_at = now() "
        " where report_id = :id",
        {"id": str(report_id), "toponyms": json.dumps(_mentions_json(mentions))},
    )


async def _write_ready(
    sessionmaker: async_sessionmaker[AsyncSession],
    report_id: uuid.UUID,
    output: ResolvedLocation,
) -> None:
    polygon = json.dumps(output.polygon) if output.polygon is not None else None
    await execute_write(
        sessionmaker,
        "update public.report_geocodes "
        "   set lat = :lat, lon = :lon, "
        "       polygon = cast(:polygon as jsonb), "
        "       granularity_tier = :tier, radius_m = :radius_m, "
        "       area_only = :area_only, confidence = :confidence, "
        "       source = :source, "
        "       toponyms = cast(:toponyms as jsonb), "
        "       status = 'ready', error = null, updated_at = now() "
        " where report_id = :id",
        {
            "id": str(report_id),
            "lat": output.lat,
            "lon": output.lon,
            "polygon": polygon,
            "tier": output.granularity_tier,
            "radius_m": output.radius_m,
            "area_only": output.area_only,
            "confidence": output.confidence,
            "source": output.source,
            "toponyms": json.dumps(toponym_audit(output.toponyms)),
        },
    )


async def _write_failed(
    sessionmaker: async_sessionmaker[AsyncSession],
    report_id: uuid.UUID,
    error: str,
) -> None:
    await mark_failed(sessionmaker, report_id, error, table="report_geocodes")


def _mentions_json(mentions: list[ToponymMention]) -> list[dict[str, Any]]:
    return [
        {
            "surface_form": m.surface_form,
            "corrected_form": m.corrected_form,
            "type_hint": m.type_hint,
        }
        for m in mentions
    ]


__all__ = ["geocode_report"]
