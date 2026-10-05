"""Per-coordinator saved search filters; opening one replays the search on live data.

api_service bypasses RLS, so every query is scoped by coordinator_id here.
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from api.admin.guards import require_crisis
from api.admin.search_filters import filter_signature
from api.auth.dependency import require_coordinator
from api.auth.models import Coordinator
from api.core.app_state import AppState, get_app_state
from api.schemas.admin_search import SearchRequest
from api.schemas.saved_views import SavedViewCreate, SavedViewResponse, SavedViewUpdate

router = APIRouter(prefix="/reports/search/views")


_INSERT_SQL = text(
    """
    insert into public.saved_search_views
        (coordinator_id, crisis_id, name, payload)
    values
        (cast(:coordinator_id as uuid), cast(:crisis_id as uuid), :name, cast(:payload as jsonb))
    returning id, crisis_id, name, payload, created_at, updated_at
    """
)

_LIST_SQL = text(
    """
    select id, crisis_id, name, payload, created_at, updated_at
      from public.saved_search_views
     where coordinator_id = cast(:coordinator_id as uuid)
       and crisis_id = cast(:crisis_id as uuid)
     order by updated_at desc, id desc
    """
)

_GET_SQL = text(
    """
    select id, crisis_id, name, payload, created_at, updated_at
      from public.saved_search_views
     where id = cast(:id as uuid)
       and coordinator_id = cast(:coordinator_id as uuid)
    """
)

_UPDATE_SQL = text(
    """
    update public.saved_search_views
       set name       = coalesce(:name, name),
           payload    = coalesce(cast(:payload as jsonb), payload),
           updated_at = now()
     where id = cast(:id as uuid)
       and coordinator_id = cast(:coordinator_id as uuid)
    returning id, crisis_id, name, payload, created_at, updated_at
    """
)

_DELETE_SQL = text(
    """
    delete from public.saved_search_views
     where id = cast(:id as uuid)
       and coordinator_id = cast(:coordinator_id as uuid)
    returning id
    """
)


def _payload_with_signature(crisis_id: uuid.UUID, filter_: SearchRequest) -> dict[str, Any]:
    """Store the signature with the filter so summary and chat caches can key on it."""
    payload = filter_.model_dump(mode="json")
    payload["filter_signature"] = filter_signature(crisis_id, filter_)
    return payload


def _row_to_response(row: Any) -> SavedViewResponse:
    raw: Any = row.payload
    if isinstance(raw, str):
        raw = json.loads(raw)
    payload: dict[str, Any] = cast(dict[str, Any], raw)
    # filter_signature is server-computed and not part of SearchRequest.
    payload_no_sig = {k: v for k, v in payload.items() if k != "filter_signature"}
    return SavedViewResponse(
        id=row.id,
        crisis_id=row.crisis_id,
        name=row.name,
        filter=SearchRequest.model_validate(payload_no_sig),
        filter_signature=payload.get("filter_signature", ""),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("", response_model=list[SavedViewResponse])
async def list_saved_views(
    state: Annotated[AppState, Depends(get_app_state)],
    coordinator: Annotated[Coordinator, Depends(require_coordinator)],
    crisis_id: Annotated[uuid.UUID, Query(description="Crisis whose views to list.")],
) -> list[SavedViewResponse]:
    async with state.sessionmaker() as session:
        result = await session.execute(
            _LIST_SQL,
            {"coordinator_id": str(coordinator.id), "crisis_id": str(crisis_id)},
        )
        return [_row_to_response(r) for r in result.all()]


@router.post("", response_model=SavedViewResponse, status_code=201)
async def create_saved_view(
    state: Annotated[AppState, Depends(get_app_state)],
    coordinator: Annotated[Coordinator, Depends(require_coordinator)],
    payload: SavedViewCreate,
    crisis_id: Annotated[uuid.UUID, Query(description="Crisis the view belongs to.")],
) -> SavedViewResponse:
    async with state.sessionmaker() as session, session.begin():
        await require_crisis(session, crisis_id)
        body = _payload_with_signature(crisis_id, payload.filter)
        result = await session.execute(
            _INSERT_SQL,
            {
                "coordinator_id": str(coordinator.id),
                "crisis_id": str(crisis_id),
                "name": payload.name,
                "payload": json.dumps(body),
            },
        )
        row = result.first()
    if row is None:
        raise HTTPException(status_code=500, detail="failed to create saved view")
    return _row_to_response(row)


@router.get("/{view_id}", response_model=SavedViewResponse)
async def get_saved_view(
    state: Annotated[AppState, Depends(get_app_state)],
    coordinator: Annotated[Coordinator, Depends(require_coordinator)],
    view_id: uuid.UUID,
) -> SavedViewResponse:
    async with state.sessionmaker() as session:
        result = await session.execute(
            _GET_SQL, {"id": str(view_id), "coordinator_id": str(coordinator.id)}
        )
        row = result.first()
    if row is None:
        raise HTTPException(status_code=404, detail="saved view not found")
    return _row_to_response(row)


@router.patch("/{view_id}", response_model=SavedViewResponse)
async def patch_saved_view(
    state: Annotated[AppState, Depends(get_app_state)],
    coordinator: Annotated[Coordinator, Depends(require_coordinator)],
    view_id: uuid.UUID,
    payload: SavedViewUpdate,
) -> SavedViewResponse:
    if payload.name is None and payload.filter is None:
        raise HTTPException(status_code=400, detail="nothing to update")
    async with state.sessionmaker() as session, session.begin():
        # The signature recompute needs the view's crisis_id.
        existing = (
            await session.execute(
                _GET_SQL, {"id": str(view_id), "coordinator_id": str(coordinator.id)}
            )
        ).first()
        if existing is None:
            raise HTTPException(status_code=404, detail="saved view not found")
        payload_json: str | None = None
        if payload.filter is not None:
            body = _payload_with_signature(existing.crisis_id, payload.filter)
            payload_json = json.dumps(body)
        result = await session.execute(
            _UPDATE_SQL,
            {
                "id": str(view_id),
                "coordinator_id": str(coordinator.id),
                "name": payload.name,
                "payload": payload_json,
            },
        )
        row = result.first()
    if row is None:
        raise HTTPException(status_code=404, detail="saved view not found")
    return _row_to_response(row)


@router.delete("/{view_id}", status_code=204)
async def delete_saved_view(
    state: Annotated[AppState, Depends(get_app_state)],
    coordinator: Annotated[Coordinator, Depends(require_coordinator)],
    view_id: uuid.UUID,
) -> None:
    async with state.sessionmaker() as session, session.begin():
        result = await session.execute(
            _DELETE_SQL, {"id": str(view_id), "coordinator_id": str(coordinator.id)}
        )
        if result.first() is None:
            raise HTTPException(status_code=404, detail="saved view not found")
