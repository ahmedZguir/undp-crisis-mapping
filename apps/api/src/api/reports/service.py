"""Report submission: validate, upload photo, write row, enqueue enrichment.

The crisis is checked before upload so a bad crisis_id never writes to Storage.
Upload precedes the row write: an orphan blob is harmless, a row with a missing
photo is not.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.buildings.resolver import BuildingResolver
from api.core.arq import JobEnqueuer
from api.core.storage import StorageUploader
from api.crises.service import CrisisService
from api.reports.photo import validate_photo
from api.reports.writer import IdempotentReportWriter
from api.schemas import LocationOut
from api.schemas.reports import ReportCreatedResponse, ReportSubmitPayload


class ReportContentError(Exception):
    """The submission failed the minimum-content rule. Mapped to 422.

    Checked here, not in the payload validator, because that runs before the
    honeypot check and would let a bot tell a trapped submission from a rejected one.
    """


_logger = logging.getLogger(__name__)


class ReportSubmissionService:
    def __init__(
        self,
        storage: StorageUploader,
        crisis_service: CrisisService,
        sessionmaker: async_sessionmaker[AsyncSession],
        enrichment_enqueuer: JobEnqueuer,
        writer: IdempotentReportWriter | None = None,
    ) -> None:
        self._storage = storage
        self._crisis_service = crisis_service
        self._sessionmaker = sessionmaker
        self._writer = writer or IdempotentReportWriter(sessionmaker=sessionmaker)
        self._enrichment_enqueuer = enrichment_enqueuer

    async def submit(
        self,
        photo_content: bytes | None,
        photo_content_type: str | None,
        payload: ReportSubmitPayload,
    ) -> ReportCreatedResponse:
        content_type = photo_content_type or "application/octet-stream"
        if photo_content is not None:
            validate_photo(content_type, len(photo_content))
        await self._crisis_service.require_active(payload.crisis_id)
        self._validate_minimum_content(photo_content is not None, payload)
        photo_path = (
            await self._storage.upload_photo(photo_content, content_type)
            if photo_content is not None
            else None
        )

        async with self._sessionmaker() as session:
            building_id = await self._resolve_building_id(payload, session)

        written = await self._writer.write(
            payload=payload,
            photo_path=photo_path,
            building_id=building_id,
        )

        # Duplicates already have an enrichment run; the writer creates the sidecar
        # rows in the same transaction, so enqueueing right after commit is safe.
        if not written.was_duplicate:
            try:
                await self._enrichment_enqueuer.enqueue(written.id)
            except Exception:
                # The report is already saved; do not fail the submission.
                _logger.exception("enrichment.enqueue_failed report_id=%s", written.id)

        # Built from the payload to avoid a PostGIS round-trip.
        location_out = (
            LocationOut(lat=payload.location.lat, lng=payload.location.lng)
            if payload.location is not None
            else None
        )

        return ReportCreatedResponse(
            id=written.id,
            crisis_id=written.crisis_id,
            created_at=written.created_at,
            damage_class=payload.damage_class,
            description=payload.description,
            route_description=payload.route_description,
            location=location_out,
            infra_type=payload.infra_type,
            infra_name=payload.infra_name,
            crisis_type=payload.crisis_type,
            crisis_type_detailed=payload.crisis_type_detailed,
            debris=payload.debris,
            building_id=building_id,
        )

    @staticmethod
    def _validate_minimum_content(photo_present: bool, payload: ReportSubmitPayload) -> None:
        """(photo OR description) AND (location OR route_description).

        damage_class and required form questions are validated elsewhere.
        """
        has_description = bool(payload.description and payload.description.strip())
        if not (photo_present or has_description):
            raise ReportContentError("photo_or_description_required")
        has_location = payload.location is not None
        has_route = bool(payload.route_description and payload.route_description.strip())
        if not (has_location or has_route):
            raise ReportContentError("location_or_route_required")

    async def _resolve_building_id(
        self,
        payload: ReportSubmitPayload,
        session: AsyncSession,
    ) -> uuid.UUID | None:
        """Map the payload to a buildings.id, or None.

        An explicit building_id is resolved as a GERS id and never replaced by
        snapping, even if unknown (logged, returns None). Otherwise a location
        snaps to the nearest building within 50 m.
        """
        resolver = BuildingResolver(session)
        if payload.building_id is not None:
            resolved = await resolver.resolve_gers(payload.building_id)
            if resolved is None:
                _logger.warning(
                    "report.building_id.unknown_gers",
                    extra={
                        "event": "report.building_id.unknown_gers",
                        "source_id": payload.building_id,
                    },
                )
            return resolved
        if payload.location is not None:
            return await resolver.snap_to_nearest(payload.location, max_m=50)
        return None
