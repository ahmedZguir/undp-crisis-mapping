"""Division-name autocomplete for the location filter."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from api.admin.name_search_queries import search_divisions_by_name
from api.core.app_state import AppState, get_app_state
from api.schemas.admin_search import DivisionSearchHit

router = APIRouter(prefix="/divisions")


@router.get("/search", response_model=list[DivisionSearchHit])
async def search_divisions(
    state: Annotated[AppState, Depends(get_app_state)],
    q: Annotated[str, Query(min_length=1, description="Division-name substring to match.")],
    country_code: Annotated[
        str | None,
        Query(min_length=2, max_length=2, description="Optional ISO-2 narrowing filter."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[DivisionSearchHit]:
    async with state.sessionmaker() as session:
        return await search_divisions_by_name(session, country_code=country_code, q=q, limit=limit)
