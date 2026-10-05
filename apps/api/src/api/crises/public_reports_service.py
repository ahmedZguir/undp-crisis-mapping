"""Public-safe report reads for crises in `full` visibility mode.

Coordinator-only columns (route_description, client ids, EXIF) are never selected.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.core.listing import Bbox, decode_cursor, encode_cursor, parse_bbox
from api.core.storage import DEFAULT_SIGNED_URL_TTL_SECONDS, PhotoUrlSigner
from api.crises.service import CrisisNotFoundError, CrisisService
from api.schemas import as_damage_class, as_debris
from api.schemas.common import location_or_none
from api.schemas.public_reports import (
    PublicReportDetail,
    PublicReportListItem,
    PublicReportListResponse,
)


class ReportNotFoundError(LookupError):
    """Report missing, hidden, or its crisis no longer public.

    All cases map to the same 404 so callers cannot tell them apart.
    """


_LIST_SQL = text(
    """
    select
        r.id, r.crisis_id, r.damage_class, r.description,
        r.infra_type, r.infra_name,
        r.crisis_type, r.crisis_type_detailed, r.debris,
        r.building_id, r.created_at,
        st_y(r.location::geometry) as report_lat,
        st_x(r.location::geometry) as report_lng,
        st_y(b.centroid::geometry) as building_lat,
        st_x(b.centroid::geometry) as building_lng
    from public.reports r
    left join public.buildings b on b.id = r.building_id
    where r.crisis_id = :crisis_id
      and r.public_visible = true
      and (
          cast(:allowed_infra_types as text[]) is null
          or (
              cardinality(r.infra_type) > 0
              and r.infra_type <@ cast(:allowed_infra_types as text[])
          )
      )
      and (
          cast(:cursor_ts as timestamptz) is null
          or (r.created_at, r.id) < (cast(:cursor_ts as timestamptz), cast(:cursor_id as uuid))
      )
    order by r.created_at desc, r.id desc
    limit :fetch_limit
    """
)


_BBOX_SQL = text(
    """
    with candidates as (
        select
            r.id, r.crisis_id, r.damage_class, r.description,
            r.infra_type, r.infra_name,
            r.crisis_type, r.crisis_type_detailed, r.debris,
            r.building_id, r.created_at,
            st_y(r.location::geometry) as report_lat,
            st_x(r.location::geometry) as report_lng,
            st_y(b.centroid::geometry) as building_lat,
            st_x(b.centroid::geometry) as building_lng,
            r.map_point as map_geog
          from public.reports r
          left join public.buildings b on b.id = r.building_id
         where r.crisis_id = :crisis_id
           and r.public_visible = true
           and (
               cast(:allowed_infra_types as text[]) is null
               or (
                   cardinality(r.infra_type) > 0
                   and r.infra_type <@ cast(:allowed_infra_types as text[])
               )
           )
           -- The public map excludes AI geocodes; only pins and building
           -- centroids are shown.
           and r.map_point_source in ('submitted_pin', 'building_centroid')
           and st_intersects(
               r.map_point,
               st_makeenvelope(:west, :south, :east, :north, 4326)::geography
           )
    )
    select *, count(*) over () as total_in_bbox
      from candidates
     order by created_at desc, id desc
     limit :limit
    """
)


# The crisis gate is evaluated on the same row as the report, so a coordinator
# making the crisis private takes effect without a race between two queries.
_DETAIL_SQL = text(
    """
    select
        r.id, r.crisis_id, r.damage_class, r.description,
        r.infra_type, r.infra_name,
        r.crisis_type, r.crisis_type_detailed, r.debris,
        r.building_id, r.photo_path, r.created_at, r.public_visible,
        st_y(r.location::geometry) as report_lat,
        st_x(r.location::geometry) as report_lng,
        st_y(b.centroid::geometry) as building_lat,
        st_x(b.centroid::geometry) as building_lng,
        c.status as crisis_status,
        c.public_visibility as crisis_public_visibility,
        c.public_infra_types as crisis_public_infra_types
      from public.reports r
      left join public.buildings b on b.id = r.building_id
      join public.crises c on c.id = r.crisis_id
     where r.id = :report_id
    """
)


class PublicReportsService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        crisis_service: CrisisService,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._crisis_service = crisis_service

    async def list_for_crisis(
        self,
        *,
        crisis_id: uuid.UUID,
        bbox: str | None,
        cursor: str | None,
        limit: int,
    ) -> PublicReportListResponse:
        """Return one page of reports via bbox or cursor (the route rejects both).

        Raises CrisisNotFoundError unless the crisis is active and in `full` mode.
        """
        async with self._sessionmaker() as session:
            crisis = await self._crisis_service.require_public_visible(
                crisis_id, "full", _session=session
            )
            if bbox is not None:
                return await self._list_by_bbox(
                    session,
                    crisis_id=crisis_id,
                    bbox=parse_bbox(bbox),
                    limit=limit,
                    allowed_infra_types=crisis.public_infra_types,
                )
            return await self._list_by_cursor(
                session,
                crisis_id=crisis_id,
                cursor=cursor,
                limit=limit,
                allowed_infra_types=crisis.public_infra_types,
            )

    async def get_detail(
        self,
        report_id: uuid.UUID,
        *,
        signer: PhotoUrlSigner,
    ) -> PublicReportDetail:
        async with self._sessionmaker() as session:
            row = (await session.execute(_DETAIL_SQL, {"report_id": str(report_id)})).first()

        if row is None:
            raise ReportNotFoundError(str(report_id))
        if row.crisis_status != "active":
            raise ReportNotFoundError(str(report_id))
        if row.crisis_public_visibility != "full":
            raise ReportNotFoundError(str(report_id))
        if row.public_visible is False:
            raise ReportNotFoundError(str(report_id))
        allowed_raw = row.crisis_public_infra_types
        if allowed_raw is not None:
            allowed: set[str] = set(allowed_raw)
            report_types: list[str] = list(row.infra_type) if row.infra_type is not None else []
            # Every infra_type must be allowed; reports with none are excluded, as in SQL.
            if not report_types or not set(report_types).issubset(allowed):
                raise ReportNotFoundError(str(report_id))

        photo_url = (
            await signer.sign_photo_url(row.photo_path, DEFAULT_SIGNED_URL_TTL_SECONDS)
            if row.photo_path is not None
            else None
        )
        return PublicReportDetail(
            id=row.id,
            crisis_id=row.crisis_id,
            damage_class=as_damage_class(row.damage_class),
            description=row.description,
            infra_type=list(row.infra_type) if row.infra_type is not None else None,
            infra_name=row.infra_name,
            crisis_type=row.crisis_type,
            crisis_type_detailed=row.crisis_type_detailed,
            debris=as_debris(row.debris),
            building_id=row.building_id,
            location=location_or_none(row.report_lat, row.report_lng),
            building_centroid=location_or_none(row.building_lat, row.building_lng),
            photo_url=photo_url,
            created_at=row.created_at,
        )

    async def _list_by_cursor(
        self,
        session: AsyncSession,
        *,
        crisis_id: uuid.UUID,
        cursor: str | None,
        limit: int,
        allowed_infra_types: list[str] | None,
    ) -> PublicReportListResponse:
        cursor_ts: datetime | None = None
        cursor_id: uuid.UUID | None = None
        if cursor is not None:
            cursor_ts, cursor_id = decode_cursor(cursor)

        # Fetch one extra row to detect a next page without a count query.
        fetch_limit = limit + 1
        result = await session.execute(
            _LIST_SQL,
            {
                "crisis_id": str(crisis_id),
                "cursor_ts": cursor_ts,
                "cursor_id": str(cursor_id) if cursor_id is not None else None,
                "fetch_limit": fetch_limit,
                "allowed_infra_types": allowed_infra_types,
            },
        )
        rows = result.all()

        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = encode_cursor(page[-1].created_at, page[-1].id) if has_more and page else None

        return PublicReportListResponse(
            items=[_row_to_list_item(r) for r in page],
            next_cursor=next_cursor,
            truncated=False,
            total_in_bbox=None,
        )

    async def _list_by_bbox(
        self,
        session: AsyncSession,
        *,
        crisis_id: uuid.UUID,
        bbox: Bbox,
        limit: int,
        allowed_infra_types: list[str] | None,
    ) -> PublicReportListResponse:
        result = await session.execute(
            _BBOX_SQL,
            {
                "crisis_id": str(crisis_id),
                "west": bbox.west,
                "south": bbox.south,
                "east": bbox.east,
                "north": bbox.north,
                "limit": limit,
                "allowed_infra_types": allowed_infra_types,
            },
        )
        rows = result.all()

        total = int(rows[0].total_in_bbox) if rows else 0
        return PublicReportListResponse(
            items=[_row_to_list_item(r) for r in rows],
            next_cursor=None,
            truncated=total > limit,
            total_in_bbox=total,
        )


def _row_to_list_item(row: Any) -> PublicReportListItem:
    location = location_or_none(row.report_lat, row.report_lng)
    building_centroid = location_or_none(row.building_lat, row.building_lng)
    return PublicReportListItem(
        id=row.id,
        crisis_id=row.crisis_id,
        damage_class=as_damage_class(row.damage_class),
        description=row.description,
        infra_type=list(row.infra_type) if row.infra_type is not None else None,
        infra_name=row.infra_name,
        crisis_type=row.crisis_type,
        crisis_type_detailed=row.crisis_type_detailed,
        debris=as_debris(row.debris),
        building_id=row.building_id,
        location=location,
        map_point=location or building_centroid,
        created_at=row.created_at,
    )


__all__ = [
    "CrisisNotFoundError",
    "PublicReportsService",
    "ReportNotFoundError",
]
