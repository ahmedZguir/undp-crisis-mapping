"""Points and badges for one client_id. The client_id is trusted, not authenticated."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.reports.badges import badge_name
from api.schemas.reporter_stats import BadgeOut, ReporterStatsResponse

_TOTALS_SQL = text(
    """
    select count(*) as total_reports, coalesce(sum(points), 0) as points
    from public.report_quality
    where client_id = :client_id
    """
)

_BADGES_SQL = text(
    """
    select badge_slug, earned_at, report_id
    from public.citizen_badges
    where client_id = :client_id
    order by earned_at asc, badge_slug asc
    """
)


class ReporterStatsService:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def stats_for_client(
        self, client_id: uuid.UUID, *, since: datetime | None = None
    ) -> ReporterStatsResponse:
        async with self._sessionmaker() as session:
            totals = (await session.execute(_TOTALS_SQL, {"client_id": str(client_id)})).one()
            badge_rows = (await session.execute(_BADGES_SQL, {"client_id": str(client_id)})).all()

        badges = [
            BadgeOut(
                slug=row.badge_slug,
                name=badge_name(row.badge_slug),
                earned_at=row.earned_at,
                report_id=row.report_id,
            )
            for row in badge_rows
        ]
        newly_earned = [b for b in badges if b.earned_at > since] if since is not None else []
        return ReporterStatsResponse(
            client_id=client_id,
            total_reports=int(totals.total_reports),
            points=int(totals.points),
            badges=badges,
            newly_earned=newly_earned,
        )
