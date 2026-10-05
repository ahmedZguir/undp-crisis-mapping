from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import LocationIn


class BuildingResolver:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve_gers(self, source_id: str) -> uuid.UUID | None:
        """Internal buildings.id for an Overture GERS id, or None."""
        result = await self._session.execute(
            text(
                "select id from public.buildings "
                "where source = 'overture' and source_id = :source_id"
            ),
            {"source_id": source_id},
        )
        row = result.first()
        if row is None:
            return None
        return uuid.UUID(str(row.id))

    async def snap_to_nearest(self, point: LocationIn, max_m: int = 50) -> uuid.UUID | None:
        """Nearest building by centroid within max_m metres, or None."""
        wkt = f"POINT({point.lng} {point.lat})"
        result = await self._session.execute(
            text(
                "select id from public.buildings "
                "where ST_DWithin(centroid, ST_GeogFromText(:wkt), :max_m) "
                "order by ST_Distance(centroid, ST_GeogFromText(:wkt)) "
                "limit 1"
            ),
            {"wkt": wkt, "max_m": max_m},
        )
        row = result.first()
        if row is None:
            return None
        return uuid.UUID(str(row.id))
