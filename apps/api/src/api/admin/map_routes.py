"""Admin map endpoint: points or clusters for a viewport, chosen by zoom.

Coordinator-only, so aggregates run at k=1. Reusing this output on a public
surface must re-apply `heatmap_k_anonymity`.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from api.admin.guards import require_crisis
from api.admin.map_queries import POINTS_ZOOM, fetch_map_clusters, fetch_map_points
from api.ai import EMBEDDING_VERSION, AIClientUnavailableError, embed_query
from api.core.app_state import AppState, get_app_state
from api.schemas.admin_map import MapRequest, MapResponse

logger = logging.getLogger(__name__)

router = APIRouter()


async def _maybe_embed(payload: MapRequest, state: AppState) -> list[float] | None:
    """Cached query embedding; None without a query or when embedding is unavailable."""
    if not (payload.query and payload.query.strip()):
        return None
    key = f"{EMBEDDING_VERSION}:{payload.query.strip()}"
    cached = state.map_query_vectors.get(key)
    if cached is not None:
        return cached
    try:
        embedding = await embed_query(payload.query.strip(), client=state.ai_clients.embedding)
    except AIClientUnavailableError as exc:
        logger.warning("map.embedding_unavailable: %s", exc)
        return None
    state.map_query_vectors.put(key, embedding.vector)
    return embedding.vector


@router.post("/crises/{crisis_id}/map", response_model=MapResponse)
async def crisis_map(
    crisis_id: uuid.UUID,
    payload: MapRequest,
    state: Annotated[AppState, Depends(get_app_state)],
) -> MapResponse:
    async with state.map_concurrency, state.sessionmaker() as session:
        await require_crisis(session, crisis_id)

        query_vector = await _maybe_embed(payload, state)

        if payload.zoom >= POINTS_ZOOM:
            points, overflowed = await fetch_map_points(
                session,
                crisis_id=crisis_id,
                request=payload,
                bbox=payload.bbox,
                query_vector=query_vector,
                anomalies_only=payload.anomalies_only,
            )
            if not overflowed:
                return MapResponse(mode="points", items=points, total=len(points))
            # Too dense for points; fall through to clusters.

        cells, total, total_match_count, capped = await fetch_map_clusters(
            session,
            crisis_id=crisis_id,
            request=payload,
            bbox=payload.bbox,
            zoom=payload.zoom,
            query_vector=query_vector,
            anomalies_only=payload.anomalies_only,
        )
        return MapResponse(
            mode="clusters",
            cells=cells,
            total=total,
            total_match_count=total_match_count,
            capped=capped,
        )
