"""Public crisis list for the picker and per-crisis detail with a locale-resolved form."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from sqlalchemy import text

from api.buildings.pmtiles_extractor import public_pmtiles_url
from api.core.app_state import AppState, get_app_state
from api.core.config import get_settings
from api.core.i18n import pick_locale, resolve_schema_labels
from api.core.rate_limit import limit
from api.crises.service import CrisisService
from api.schemas.crises import CrisisListItem
from api.schemas.forms import CrisisDetailPublic

router = APIRouter()


def get_crisis_service(
    state: Annotated[AppState, Depends(get_app_state)],
) -> CrisisService:
    return state.crisis_service


@router.get("/crises", response_model=list[CrisisListItem])
@limit("60/minute")
async def list_crises(
    request: Request,
    response: Response,
    service: Annotated[CrisisService, Depends(get_crisis_service)],
) -> list[CrisisListItem]:
    response.headers["Cache-Control"] = "public, max-age=60"
    items = await service.list_active()
    # pmtiles_url is stored with the host at ingest time, which can change; rebuild on read.
    settings = get_settings()
    for item in items:
        item.pmtiles_url = public_pmtiles_url(item.pmtiles_url, settings.supabase_public_url)
    return items


@router.get("/crises/{crisis_id}", response_model=CrisisDetailPublic)
@limit("60/minute")
async def get_crisis_detail(
    crisis_id: uuid.UUID,
    request: Request,
    response: Response,
    state: Annotated[AppState, Depends(get_app_state)],
    locale: Annotated[str | None, Query()] = None,
    accept_language: Annotated[str | None, Header()] = None,
) -> CrisisDetailPublic:
    response.headers["Cache-Control"] = "public, max-age=15"
    chosen_locale = pick_locale(query=locale, accept_language=accept_language)

    # Read just (form_version, status) cheaply, then check the cache.
    async with state.sessionmaker() as session:
        row = (
            await session.execute(
                text(
                    "select id, name, status, form_version, form_schema "
                    "from public.crises where id = :id"
                ),
                {"id": str(crisis_id)},
            )
        ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="crisis_not_found")

    cache_key = (str(crisis_id), chosen_locale, int(row.form_version))
    cached = state.crisis_detail_cache.get(cache_key)
    if cached is not None:
        return CrisisDetailPublic(**cached)

    resolved_schema = resolve_schema_labels(row.form_schema, chosen_locale)
    payload: dict[str, Any] = {
        "id": str(row.id),
        "name": row.name,
        "status": row.status,
        "form_version": int(row.form_version),
        "form_schema": resolved_schema,
    }
    state.crisis_detail_cache.put(cache_key, payload)
    return CrisisDetailPublic(**payload)
