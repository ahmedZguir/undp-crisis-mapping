"""Report history for one client_id, with signed photo URLs and quality scores."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.core.storage import DEFAULT_SIGNED_URL_TTL_SECONDS, PhotoUrlSigner, StorageError
from api.reports.quality import Quality, point_factors
from api.schemas import LocationOut
from api.schemas.reports import (
    CitizenReportHistoryItem,
    CitizenReportHistoryResponse,
    CitizenReportQuality,
    ReportPointFactor,
)

logger = logging.getLogger(__name__)

# Also the route's upper bound for `limit`.
MAX_HISTORY_ITEMS = 100


_LIST_BY_CLIENT_SQL = text(
    """
    select
        r.id,
        r.crisis_id,
        r.damage_class,
        r.description,
        r.route_description,
        r.infra_type,
        r.infra_name,
        r.crisis_type,
        r.crisis_type_detailed,
        r.debris,
        r.building_id,
        r.photo_path,
        r.client_submission_id,
        r.created_at,
        st_y(r.location::geometry) as report_lat,
        st_x(r.location::geometry) as report_lng,
        c.name as crisis_name,
        c.status as crisis_status,
        rq.report_id as q_report_id,
        rq.computed_at as q_computed_at,
        rq.points as q_points,
        rq.has_photo as q_has_photo,
        rq.has_description as q_has_description,
        rq.relevance_label as q_relevance_label,
        rq.damage_agreement as q_damage_agreement,
        rq.is_duplicate_image as q_is_duplicate_image,
        rq.verified as q_verified
    from public.reports r
    join public.crises c on c.id = r.crisis_id
    left join public.report_quality rq on rq.report_id = r.id
    where r.client_id = :client_id
    order by r.created_at desc, r.id desc
    limit :limit
    """
)


class CitizenReportsHistoryService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        url_signer: PhotoUrlSigner,
        ttl_seconds: int = DEFAULT_SIGNED_URL_TTL_SECONDS,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._url_signer = url_signer
        self._ttl_seconds = ttl_seconds

    async def list_by_client(
        self, client_id: uuid.UUID, limit: int = MAX_HISTORY_ITEMS
    ) -> CitizenReportHistoryResponse:
        async with self._sessionmaker() as session:
            rows = (
                await session.execute(
                    _LIST_BY_CLIENT_SQL,
                    {"client_id": str(client_id), "limit": limit},
                )
            ).all()

        items = [await self._row_to_item(row) for row in rows]
        return CitizenReportHistoryResponse(items=items, total=len(items))

    async def _row_to_item(self, row: Any) -> CitizenReportHistoryItem:
        # An unsignable photo (orphaned blob) nulls this row's URL instead of failing the feed.
        photo_url: str | None = None
        if row.photo_path is not None:
            try:
                photo_url = await self._url_signer.sign_photo_url(row.photo_path, self._ttl_seconds)
            except StorageError:
                logger.warning(
                    "history.photo_sign_failed report_id=%s photo_path=%s",
                    row.id,
                    row.photo_path,
                )
                photo_url = None
        location = (
            LocationOut(lat=row.report_lat, lng=row.report_lng)
            if row.report_lat is not None and row.report_lng is not None
            else None
        )
        return CitizenReportHistoryItem(
            id=row.id,
            crisis_id=row.crisis_id,
            crisis_name=row.crisis_name,
            crisis_status=row.crisis_status,
            created_at=row.created_at,
            damage_class=row.damage_class,
            description=row.description,
            route_description=row.route_description,
            location=location,
            infra_type=row.infra_type,
            infra_name=row.infra_name,
            crisis_type=row.crisis_type,
            crisis_type_detailed=row.crisis_type_detailed,
            debris=row.debris,
            building_id=row.building_id,
            photo_url=photo_url,
            client_submission_id=row.client_submission_id,
            quality=self._build_quality(row),
        )

    @staticmethod
    def _build_quality(row: Any) -> CitizenReportQuality | None:
        # No report_quality row: show nothing rather than a fake zero score.
        if row.q_report_id is None:
            return None
        # computed_at is null until the scoring worker runs; the factor columns are
        # still defaults then, so the client shows an "analyzing" state.
        scored = row.q_computed_at is not None
        factors: list[ReportPointFactor] = []
        if scored:
            q = Quality(
                has_photo=bool(row.q_has_photo),
                has_description=bool(row.q_has_description),
                relevance_label=row.q_relevance_label,
                damage_agreement=row.q_damage_agreement,
                photo_fresh=None,
                gps_pin_match=None,
                corroborators_500m=0,
                reporter_reputation=0,
                is_duplicate_image=bool(row.q_is_duplicate_image),
            )
            factors = [ReportPointFactor(key=f.key, state=f.state) for f in point_factors(q)]
        return CitizenReportQuality(
            scored=scored,
            points=int(row.q_points or 0),
            verified=bool(row.q_verified),
            is_duplicate_image=bool(row.q_is_duplicate_image),
            factors=factors,
        )
