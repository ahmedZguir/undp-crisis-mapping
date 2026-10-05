"""Admin form-schema read and publish for a crisis."""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from api.ai.client import build_ai_clients
from api.ai.translation import translate_to_all_locales
from api.core.app_state import AppState, get_app_state
from api.core.config import get_settings
from api.core.i18n import SUPPORTED_LOCALES, pick_locale, resolve_schema_labels
from api.core.rate_limit import limit
from api.forms.translation_diff import merge_translations
from api.forms.validation import validate_form_schema
from api.schemas.admin_forms import (
    FormGetResponse,
    FormPublishPayload,
    FormPublishResponse,
)

router = APIRouter(prefix="/crises")


@router.get("/{crisis_id}/form", response_model=FormGetResponse)
@limit("60/minute")
async def get_form(
    crisis_id: uuid.UUID,
    request: Request,
    state: Annotated[AppState, Depends(get_app_state)],
    locale: Annotated[str | None, Query()] = None,
    accept_language: Annotated[str | None, Header()] = None,
) -> FormGetResponse:
    chosen_locale = pick_locale(query=locale, accept_language=accept_language)

    async with state.sessionmaker() as session:
        row = (
            await session.execute(
                text("select form_version, form_schema from public.crises where id = :id"),
                {"id": str(crisis_id)},
            )
        ).first()

    if row is None:
        raise HTTPException(status_code=404, detail="crisis_not_found")

    resolved = resolve_schema_labels(row.form_schema, chosen_locale)
    return FormGetResponse(version=int(row.form_version), schema=resolved)


# Form editing is crisis work, so the router-level coordinator gate is enough.
@router.post("/{crisis_id}/form")
@limit("30/minute")
async def publish_form(
    crisis_id: uuid.UUID,
    request: Request,
    payload: FormPublishPayload,
    state: Annotated[AppState, Depends(get_app_state)],
    locale: Annotated[str | None, Query()] = None,
    accept_language: Annotated[str | None, Header()] = None,
) -> Any:

    errors = validate_form_schema(payload.form_schema)
    if errors:
        raise HTTPException(status_code=400, detail={"errors": errors})

    chosen_locale = pick_locale(query=locale, accept_language=accept_language)

    async with state.sessionmaker() as session, session.begin():
        # FOR UPDATE serialises concurrent publishes; the loser sees the bumped version.
        row = (
            await session.execute(
                text("select form_version from public.crises where id = :id for update"),
                {"id": str(crisis_id)},
            )
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail="crisis_not_found")

        current_version = int(row.form_version)
        if payload.based_on_version != current_version:
            schema_row = (
                await session.execute(
                    text("select form_schema from public.crises where id = :id"),
                    {"id": str(crisis_id)},
                )
            ).first()
            assert schema_row is not None
            return JSONResponse(
                status_code=409,
                content={
                    "current_version": current_version,
                    "schema": resolve_schema_labels(schema_row.form_schema, chosen_locale),
                },
            )

        # Unchanged source strings reuse their existing translations.
        prior_row = (
            await session.execute(
                text("select form_schema from public.crises where id = :id"),
                {"id": str(crisis_id)},
            )
        ).first()
        prior_schema = prior_row.form_schema if prior_row is not None else {}

        # Built per publish, which is rare. Unconfigured clients fall back to the source string.
        ai_clients = build_ai_clients(get_settings())

        async_results: dict[str, dict[str, str]] = {}

        async def _translate_all(source: str) -> None:
            async_results[source] = await translate_to_all_locales(
                source, list(SUPPORTED_LOCALES), clients=ai_clients
            )

        # merge_translations takes a sync callback, so first collect the strings
        # that need translating, translate them async, then merge for real.
        pending_sources: list[str] = []

        def _record(source: str) -> dict[str, str]:
            pending_sources.append(source)
            return {"_pending_": source}

        merge_translations(payload.form_schema, prior_schema, _record)

        for src in set(pending_sources):
            await _translate_all(src)

        def _real_translate(source: str) -> dict[str, str]:
            return async_results.get(source, {locale: source for locale in SUPPORTED_LOCALES})

        merged_schema = merge_translations(payload.form_schema, prior_schema, _real_translate)

        new_version = current_version + 1
        schema_json = json.dumps(merged_schema)

        await session.execute(
            text(
                "update public.crises set form_version = :v, form_schema = cast(:s as jsonb) "
                "where id = :id"
            ),
            {"v": new_version, "s": schema_json, "id": str(crisis_id)},
        )
        await session.execute(
            text(
                "insert into public.crisis_form_versions (crisis_id, version, schema) "
                "values (:id, :v, cast(:s as jsonb))"
            ),
            {"id": str(crisis_id), "v": new_version, "s": schema_json},
        )

    # Serve the newly published schema.
    state.crisis_detail_cache.evict_where(lambda key: key[0] == str(crisis_id))
    return FormPublishResponse(
        version=new_version,
        schema=resolve_schema_labels(merged_schema, chosen_locale),
    )
