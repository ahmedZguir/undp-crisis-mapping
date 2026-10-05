"""Public crisis stats. Served for active crises in every visibility mode except none."""

from __future__ import annotations

import uuid
from typing import get_args

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.crises.service import CrisisService
from api.schemas import DamageClass
from api.schemas.public_reports import CrisisStatsResponse

_DAMAGE_CLASSES: tuple[DamageClass, ...] = get_args(DamageClass)


_TOTAL_BY_CLASS_SQL = text(
    """
    select damage_class, count(*) as n
      from public.reports
     where crisis_id = :crisis_id
     group by damage_class
    """
)


_TIME_WINDOWS_SQL = text(
    """
    select
        count(*) filter (where created_at >= now() - interval '24 hours') as last_24h,
        count(*) filter (where created_at >= now() - interval '7 days')   as last_7d,
        max(created_at)                                                    as latest_at
      from public.reports
     where crisis_id = :crisis_id
    """
)


_AFFECTED_CELLS_SQL = text(
    """
    select count(*)
      from public.heat_cells
     where crisis_id = :crisis_id
       and report_count >= (
           select heatmap_k_anonymity from public.crises where id = :crisis_id
       )
    """
)


class HeatmapStatsService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        crisis_service: CrisisService,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._crisis_service = crisis_service

    async def compute(self, crisis_id: uuid.UUID) -> CrisisStatsResponse:
        params = {"crisis_id": str(crisis_id)}

        async with self._sessionmaker() as session:
            await self._crisis_service.require_public_visible(crisis_id, _session=session)
            by_class_rows = (await session.execute(_TOTAL_BY_CLASS_SQL, params)).all()
            windows = (await session.execute(_TIME_WINDOWS_SQL, params)).one()
            affected_cells = (await session.execute(_AFFECTED_CELLS_SQL, params)).scalar_one()

        by_damage_class: dict[DamageClass, int] = {dc: 0 for dc in _DAMAGE_CLASSES}
        for row in by_class_rows:
            by_damage_class[row.damage_class] = row.n

        return CrisisStatsResponse(
            total_reports=sum(by_damage_class.values()),
            by_damage_class=by_damage_class,
            last_24h=windows.last_24h or 0,
            last_7d=windows.last_7d or 0,
            affected_cells=affected_cells,
            latest_at=windows.latest_at,
        )
