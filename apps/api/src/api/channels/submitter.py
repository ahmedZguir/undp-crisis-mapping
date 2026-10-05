"""Hands a completed channel session to ``ReportSubmissionService``.

``client_submission_id`` is the per-session UUID, never derived from the phone.
"""

from __future__ import annotations

import uuid
from typing import Protocol

import httpx
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from api.channels.media import download_twilio_media
from api.channels.plan import plan_from_schema
from api.channels.sessions import Session
from api.channels.values import CRISIS_NATURE_BUCKETS, NATURE_OTHER
from api.crises.service import CrisisArchivedError, CrisisNotFoundError
from api.reports.photo import PhotoValidationError
from api.reports.service import ReportContentError, ReportSubmissionService
from api.schemas.reports import ReportSubmitPayload


class ReportSubmitter(Protocol):
    async def submit(self, session: Session) -> uuid.UUID: ...


class SubmissionError(RuntimeError):
    pass


def build_report_data(
    session: Session, *, always_send_description: bool = False
) -> dict[str, object]:
    """Optional built-in fields are sent only when their page is enabled, matching the PWA.

    ``always_send_description``: SMS and IVR have no photo, so the description is always sent.
    """
    data: dict[str, object] = {
        "crisis_id": str(session.crisis_id),
        "damage_class": session.damage_class,
        "client_submission_id": str(session.client_submission_id),
    }
    plan = plan_from_schema(session.form_schema, session.language or "en")
    enabled_fields = {slot.target for slot in plan if not slot.is_generic}
    description_enabled = always_send_description or "infra_description" in enabled_fields
    if session.infra_description is not None and description_enabled:
        # Pydantic would silently drop an `infra_description` key.
        data["description"] = session.infra_description
    if session.debris is not None and "debris" in enabled_fields:
        data["debris"] = session.debris
    if session.infra_type and "infra_type" in enabled_fields:
        data["infra_type"] = list(session.infra_type)
        if session.infra_type_other:
            # No dedicated column; the PWA stores "other" wording in infra_name too.
            data["infra_name"] = session.infra_type_other
    if session.crisis_nature is not None and "crisis_nature" in enabled_fields:
        data["crisis_type_detailed"] = session.crisis_nature
        bucket = CRISIS_NATURE_BUCKETS.get(session.crisis_nature)
        if bucket is not None:
            data["crisis_type"] = bucket
        if session.crisis_nature == NATURE_OTHER and session.crisis_nature_other:
            data["crisis_type_detailed"] = f"Other: {session.crisis_nature_other}"
    if session.location is not None:
        lat, lng = session.location
        data["location"] = {"lat": lat, "lng": lng}
    if session.route_description:
        data["route_description"] = session.route_description
    if session.form_version is not None:
        data["form_version"] = session.form_version
    if session.generic_answers:
        data["generic_answers"] = dict(session.generic_answers)
    return data


class ServiceReportSubmitter:
    """Submits in process, so bot reports don't share one localhost rate-limit bucket."""

    def __init__(
        self,
        service: ReportSubmissionService,
        *,
        twilio_account_sid: str,
        twilio_auth_token: str,
        always_send_description: bool = False,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._service = service
        self._twilio_account_sid = twilio_account_sid
        self._twilio_auth_token = twilio_auth_token
        self._always_send_description = always_send_description
        self._client = client

    async def submit(self, session: Session) -> uuid.UUID:
        if session.crisis_id is None:
            raise SubmissionError("session has no crisis_id")
        if session.damage_class is None:
            raise SubmissionError("session has no damage_class")

        data = build_report_data(session, always_send_description=self._always_send_description)
        try:
            payload = ReportSubmitPayload.model_validate(data)
        except ValidationError as exc:
            raise SubmissionError(f"invalid report payload: {exc}") from exc

        photo_bytes: bytes | None = None
        photo_mime: str | None = None
        if session.photo_bytes is not None:
            photo_bytes, photo_mime = session.photo_bytes, session.photo_mime or "image/jpeg"
        elif session.photo_media_url is not None:
            photo_bytes, photo_mime = await download_twilio_media(
                session.photo_media_url,
                account_sid=self._twilio_account_sid,
                auth_token=self._twilio_auth_token,
                client=self._client,
            )

        try:
            created = await self._service.submit(photo_bytes, photo_mime, payload)
        except (
            ReportContentError,
            PhotoValidationError,
            CrisisNotFoundError,
            CrisisArchivedError,
            IntegrityError,
        ) as exc:
            raise SubmissionError(f"report rejected: {type(exc).__name__}: {exc}") from exc
        return created.id
