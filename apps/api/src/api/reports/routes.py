"""Citizen-facing report routes: submit, history, deletion, reporter stats."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from api.core.app_state import AppState, get_app_state
from api.core.rate_limit import ip_key, limit
from api.crises.service import CrisisArchivedError, CrisisNotFoundError
from api.reports.deletion import CitizenReportDeletionService
from api.reports.history import MAX_HISTORY_ITEMS, CitizenReportsHistoryService
from api.reports.photo import PhotoTooLargeError, UnsupportedPhotoTypeError
from api.reports.service import ReportContentError, ReportSubmissionService
from api.reports.stats import ReporterStatsService
from api.schemas.reporter_stats import ReporterStatsResponse
from api.schemas.reports import (
    CitizenReportHistoryResponse,
    ReportCreatedResponse,
    ReportDeleteByIdRequest,
    ReportDeleteRequest,
    ReportDeleteResponse,
    ReportSubmitPayload,
)

router = APIRouter()

logger = logging.getLogger(__name__)

# Raised by the reports_gps_inside_crisis trigger when GPS is outside the crisis polygon.
_POLYGON_CHECK_VIOLATION_SQLSTATE = "23514"


def _is_polygon_check_violation(exc: IntegrityError) -> bool:
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate != _POLYGON_CHECK_VIOLATION_SQLSTATE:
        return False
    # Match the message too so an unrelated 23514 on reports is not misreported.
    return "outside the crisis area" in str(exc.orig or "").lower()


def get_report_service(
    state: Annotated[AppState, Depends(get_app_state)],
) -> ReportSubmissionService:
    return state.report_service


def get_reports_history_service(
    state: Annotated[AppState, Depends(get_app_state)],
) -> CitizenReportsHistoryService:
    return state.reports_history_service


def get_report_deletion_service(
    state: Annotated[AppState, Depends(get_app_state)],
) -> CitizenReportDeletionService:
    return state.report_deletion_service


def get_reporter_stats_service(
    state: Annotated[AppState, Depends(get_app_state)],
) -> ReporterStatsService:
    return ReporterStatsService(state.sessionmaker)


def _honeypot_response(payload: ReportSubmitPayload) -> ReportCreatedResponse:
    """Fake success response, shaped like a real 200 so a bot cannot tell it was trapped."""
    return ReportCreatedResponse(
        id=uuid.uuid4(),
        crisis_id=payload.crisis_id,
        created_at=datetime.now(UTC),
        damage_class=payload.damage_class,
        description=payload.description,
        route_description=payload.route_description,
        location=None,
        infra_type=payload.infra_type,
        infra_name=payload.infra_name,
        crisis_type=payload.crisis_type,
        crisis_type_detailed=payload.crisis_type_detailed,
        debris=payload.debris,
        building_id=None,
    )


@router.post("/reports", response_model=ReportCreatedResponse)
@limit("5/minute;30/hour")
async def post_reports(
    request: Request,
    data: Annotated[str, Form(...)],
    service: Annotated[ReportSubmissionService, Depends(get_report_service)],
    photo: Annotated[UploadFile | None, File()] = None,
) -> ReportCreatedResponse:
    try:
        raw = json.loads(data)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON in `data`: {exc}") from exc

    try:
        payload = ReportSubmitPayload.model_validate(raw)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors()) from exc

    # Honeypot: the hidden `message` field is only filled by form-spamming bots.
    # The rate limiter has already counted the request.
    if payload.message:
        client_host = request.client.host if request.client else None
        logger.info(
            "honeypot.triggered client_id=%s ip=%s",
            payload.client_id,
            client_host,
        )
        return _honeypot_response(payload)

    # Photo is optional; the service enforces the minimum-content rule.
    content: bytes | None = None
    content_type: str | None = None
    if photo is not None:
        content = await photo.read()
        content_type = photo.content_type or "application/octet-stream"

    try:
        return await service.submit(content, content_type, payload)
    except ReportContentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except UnsupportedPhotoTypeError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except PhotoTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except (CrisisNotFoundError, CrisisArchivedError) as exc:
        raise HTTPException(status_code=409, detail="crisis_archived_or_unknown") from exc
    except IntegrityError as exc:
        if _is_polygon_check_violation(exc):
            raise HTTPException(
                status_code=400,
                detail="location_outside_crisis_area",
            ) from exc
        raise


@router.post("/reports/delete", response_model=ReportDeleteResponse)
@limit("1/10minutes;5/day", key_func=ip_key)
async def delete_reports_by_client(
    request: Request,
    payload: ReportDeleteRequest,
    service: Annotated[CitizenReportDeletionService, Depends(get_report_deletion_service)],
) -> ReportDeleteResponse:
    """Hard-delete every report for a client_id.

    Always returns 200 with the same body, even on internal errors, so the endpoint
    cannot be used to probe whether a client_id exists. Rate-limited by IP because
    keying by client_id would give a holder of a stolen id unlimited attempts.
    """
    try:
        await service.delete_by_client(payload.client_id)
    except Exception:
        # Swallowed so a failure is indistinguishable from success.
        logger.exception("reports.delete.failed")
    return ReportDeleteResponse()


@router.post("/reports/{report_id}/delete", response_model=ReportDeleteResponse)
@limit("3/minute;20/hour", key_func=ip_key)
async def delete_one_report(
    request: Request,
    report_id: uuid.UUID,
    payload: ReportDeleteByIdRequest,
    service: Annotated[CitizenReportDeletionService, Depends(get_report_deletion_service)],
) -> ReportDeleteResponse:
    """Delete one report if it belongs to client_id.

    A match, a wrong owner and an unknown report_id all return the same 200 body,
    so callers cannot discover which report ids exist or who owns them.
    """
    try:
        await service.delete_one(report_id, payload.client_id)
    except Exception:
        logger.exception("reports.delete_one.failed")
    return ReportDeleteResponse()


@router.get("/reports", response_model=CitizenReportHistoryResponse)
async def list_reports_by_client(
    client_id: Annotated[uuid.UUID, Query(description="The PWA's anonymous client_id.")],
    service: Annotated[CitizenReportsHistoryService, Depends(get_reports_history_service)],
    limit: Annotated[int, Query(ge=1, le=MAX_HISTORY_ITEMS)] = MAX_HISTORY_ITEMS,
) -> CitizenReportHistoryResponse:
    """Most recent reports for a client_id across all crises.

    The client_id is trusted, not authenticated. Unknown and empty clients both
    return an empty list.
    """
    return await service.list_by_client(client_id, limit=limit)


@router.get("/me/stats", response_model=ReporterStatsResponse)
async def get_reporter_stats(
    client_id: Annotated[uuid.UUID, Query(description="The PWA's anonymous client_id.")],
    service: Annotated[ReporterStatsService, Depends(get_reporter_stats_service)],
    since: Annotated[
        datetime | None,
        Query(description="Watermark: badges earned after this are returned in newly_earned."),
    ] = None,
) -> ReporterStatsResponse:
    """Points and badges for a client_id (trusted, not authenticated).

    `since` is the newest earned_at the client has shown; later badges come back
    in newly_earned.
    """
    return await service.stats_for_client(client_id, since=since)
