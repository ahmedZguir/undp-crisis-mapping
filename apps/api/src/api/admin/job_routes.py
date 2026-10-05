"""Admin building-ingest jobs: enqueue, estimate, and status from `public.crisis_jobs`."""

from __future__ import annotations

import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from api.buildings.ingest_estimator import EstimateUnavailableError, IngestEstimator
from api.buildings.overture_building_reader import DuckDBBuildingsReader
from api.buildings.overture_release_locator import OvertureReleaseLocator
from api.core.app_state import AppState, get_app_state
from api.core.arq import ArqEnqueuer, JobEnqueuer
from api.core.config import get_settings
from api.core.rate_limit import limit
from api.schemas.crises import (
    CrisisJobOut,
    IngestEstimateGeometryRequest,
    IngestEstimateResponse,
)

router = APIRouter()


def get_building_ingest_enqueuer(
    state: Annotated[AppState, Depends(get_app_state)],
) -> JobEnqueuer:
    return ArqEnqueuer(state.arq_pool, "ingest_buildings")


def get_ingest_estimator(
    state: Annotated[AppState, Depends(get_app_state)],
) -> IngestEstimator:
    # Cheap per request: neither the reader nor the locator connects until used.
    settings = get_settings()
    return IngestEstimator(
        sessionmaker=state.sessionmaker,
        reader=DuckDBBuildingsReader(),
        release_locator=OvertureReleaseLocator(),
        download_rows_per_sec=settings.ingest_est_download_rows_per_sec,
        load_rows_per_sec=settings.ingest_est_load_rows_per_sec,
    )


@router.post("/crises/{crisis_id}/ingest_buildings", status_code=202)
@limit("30/minute")
async def post_ingest_buildings(
    request: Request,
    crisis_id: uuid.UUID,
    enqueuer: Annotated[JobEnqueuer, Depends(get_building_ingest_enqueuer)],
    state: Annotated[AppState, Depends(get_app_state)],
    force: bool = Query(default=False),
) -> JSONResponse:
    """Enqueue a building ingest; 409 if one is already running.

    The `running` row and the enqueue share a transaction, so a failed enqueue
    leaves no row. `force=true` recovers from a worker that died mid-run.
    """
    async with state.sessionmaker() as session, session.begin():
        existing = (
            await session.execute(
                text(
                    "select status, started_at "
                    "from public.crisis_jobs "
                    "where crisis_id = :id and job_type = 'ingest_buildings'"
                ),
                {"id": str(crisis_id)},
            )
        ).first()
        if existing is not None and existing.status == "running" and not force:
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "ingest already running",
                    "started_at": existing.started_at.isoformat(),
                },
            )

        await session.execute(
            text(
                "insert into public.crisis_jobs "
                "  (crisis_id, job_type, status, error, started_at, ended_at) "
                "values (:id, 'ingest_buildings', 'running', null, now(), null) "
                "on conflict (crisis_id, job_type) do update set "
                "  status = 'running', error = null, "
                "  started_at = now(), ended_at = null"
            ),
            {"id": str(crisis_id)},
        )
        job_id = await enqueuer.enqueue(crisis_id)

    return JSONResponse(status_code=202, content={"job_id": job_id})


@router.post(
    "/crises/{crisis_id}/ingest_buildings/estimate",
    response_model=IngestEstimateResponse,
)
@limit("30/minute")
async def estimate_ingest_buildings(
    request: Request,
    crisis_id: uuid.UUID,
    estimator: Annotated[IngestEstimator, Depends(get_ingest_estimator)],
) -> IngestEstimateResponse:
    """Approximate building count and per-phase duration; 422 if the crisis has no geometry."""
    try:
        return await estimator.estimate(crisis_id)
    except EstimateUnavailableError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/buildings/ingest_estimate", response_model=IngestEstimateResponse)
@limit("30/minute")
async def estimate_ingest_buildings_for_geometry(
    request: Request,
    body: IngestEstimateGeometryRequest,
    estimator: Annotated[IngestEstimator, Depends(get_ingest_estimator)],
) -> IngestEstimateResponse:
    """Same estimate for an unsaved geometry, used by the crisis create flow."""
    geom_type = body.geometry.get("type")
    if geom_type not in ("Polygon", "MultiPolygon"):
        raise HTTPException(status_code=422, detail="geometry must be a Polygon or MultiPolygon")
    try:
        return await estimator.estimate_geometry(json.dumps(body.geometry))
    except EstimateUnavailableError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/crises/{crisis_id}/jobs", response_model=list[CrisisJobOut])
async def list_crisis_jobs(
    crisis_id: uuid.UUID,
    state: Annotated[AppState, Depends(get_app_state)],
) -> list[CrisisJobOut]:
    async with state.sessionmaker() as session:
        result = await session.execute(
            text(
                "select job_type, status, error, started_at, ended_at, "
                "       phase, progress_count, progress_total "
                "from public.crisis_jobs "
                "where crisis_id = :id "
                "order by started_at desc"
            ),
            {"id": str(crisis_id)},
        )
        rows = result.all()

    return [
        CrisisJobOut(
            job_type=r.job_type,
            status=r.status,
            error=r.error,
            started_at=r.started_at,
            ended_at=r.ended_at,
            phase=r.phase,
            progress_count=r.progress_count,
            progress_total=r.progress_total,
        )
        for r in rows
    ]
