"""Admin buildings layer: per-building report stats and building-name search.

The stats endpoint requires a bbox; it is viewport-bound like the reports list.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from api.admin.building_queries import list_building_stats
from api.admin.guards import require_crisis
from api.admin.name_search_queries import search_buildings_by_name
from api.ai import AIClientUnavailableError, embed_query
from api.core.app_state import AppState, get_app_state
from api.core.listing import Bbox
from api.schemas.admin_buildings import AdminBuildingsFeatureCollection, BuildingStatsRequest
from api.schemas.admin_search import BuildingSearchHit

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/crises/{crisis_id}/buildings/stats",
    response_model=AdminBuildingsFeatureCollection,
)
async def list_buildings_with_stats(
    crisis_id: uuid.UUID,
    payload: BuildingStatsRequest,
    state: Annotated[AppState, Depends(get_app_state)],
) -> AdminBuildingsFeatureCollection:
    """Per-building damage stats for the viewport under the full filter payload.

    POST because polygons and the semantic query don't fit in query params.
    """
    west, south, east, north = payload.bbox
    parsed_bbox = Bbox(west=west, south=south, east=east, north=north)
    async with state.sessionmaker() as session:
        await require_crisis(session, crisis_id)

        query_vector: list[float] | None = None
        if payload.query and payload.query.strip():
            try:
                embedding = await embed_query(
                    payload.query.strip(), client=state.ai_clients.embedding
                )
                query_vector = embedding.vector
            except AIClientUnavailableError as exc:
                # Degrade like the search/map surfaces: drop the semantic term.
                logger.warning("buildings_stats.embedding_unavailable: %s", exc)
                query_vector = None

        return await list_building_stats(
            session,
            crisis_id=crisis_id,
            request=payload,
            bbox=parsed_bbox,
            query_vector=query_vector,
            limit=payload.limit,
        )


@router.get("/buildings/search", response_model=list[BuildingSearchHit])
async def search_buildings(
    state: Annotated[AppState, Depends(get_app_state)],
    crisis_id: Annotated[uuid.UUID, Query(description="Crisis to scope the search to.")],
    q: Annotated[str, Query(min_length=1, description="Building-name substring to match.")],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[BuildingSearchHit]:
    """Fuzzy-match building names within a crisis, for the building filter autocomplete."""
    async with state.sessionmaker() as session:
        await require_crisis(session, crisis_id)
        return await search_buildings_by_name(session, crisis_id=crisis_id, q=q, limit=limit)
