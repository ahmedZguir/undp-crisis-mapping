"""Pin aggregation for the public buildings-mode map.

Each pin is a damaged building (reports grouped by building) or a freeform
report, labelled with the worst damage class in the group.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.crises.service import CrisisService
from api.schemas import as_damage_class
from api.schemas.public_reports import (
    BuildingFeature,
    BuildingFeatureProperties,
    BuildingsFeatureCollection,
    PointGeometry,
)

# Pins group by building_id for matched reports and by report id otherwise.
# Reports with neither a building centroid nor a location are dropped.
# max(lat/lng/building_id) is safe: every row in a group shares the same values.
_PINS_SQL = text(
    """
    with rows_with_pin as (
        select
            r.id              as report_id,
            r.building_id,
            r.damage_class,
            st_y(coalesce(b.centroid, r.location)::geometry) as lat,
            st_x(coalesce(b.centroid, r.location)::geometry) as lng,
            coalesce(r.building_id::text, r.id::text) as pin_key
          from public.reports r
          left join public.buildings b on b.id = r.building_id
         where r.crisis_id = :crisis_id
           and r.public_visible = true
           and coalesce(b.centroid, r.location) is not null
           and (
               cast(:allowed_infra_types as text[]) is null
               or (
                   cardinality(r.infra_type) > 0
                   and r.infra_type <@ cast(:allowed_infra_types as text[])
               )
           )
    )
    select
        pin_key,
        case
            when bool_or(damage_class = 'complete') then 'complete'
            when bool_or(damage_class = 'partial')  then 'partial'
            else 'minimal'
        end as damage_class,
        count(*) as report_count,
        max(building_id::text) as building_id,
        max(lat) as lat,
        max(lng) as lng
      from rows_with_pin
     group by pin_key
    """
)


class PublicBuildingsService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        crisis_service: CrisisService,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._crisis_service = crisis_service

    async def list_pins(self, crisis_id: uuid.UUID) -> BuildingsFeatureCollection:
        """Raises CrisisNotFoundError unless the crisis is active and in `buildings` mode."""
        async with self._sessionmaker() as session:
            crisis = await self._crisis_service.require_public_visible(
                crisis_id, "buildings", _session=session
            )
            rows = (
                await session.execute(
                    _PINS_SQL,
                    {
                        "crisis_id": str(crisis_id),
                        "allowed_infra_types": crisis.public_infra_types,
                    },
                )
            ).all()

        features = [
            BuildingFeature(
                geometry=PointGeometry(coordinates=(float(row.lng), float(row.lat))),
                properties=BuildingFeatureProperties(
                    damage_class=as_damage_class(row.damage_class),
                    report_count=int(row.report_count),
                    building_id=row.building_id,
                ),
            )
            for row in rows
        ]
        return BuildingsFeatureCollection(features=features)
