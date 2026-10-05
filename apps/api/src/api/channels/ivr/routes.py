"""Twilio voice webhooks: /ivr/voice starts a call, /ivr/input takes every Gather/Record.

Returns 503 when Twilio is unconfigured. Signatures are checked against IVR_PUBLIC_URL
when set, since Twilio signs the public URL.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, Response

from api.channels.ivr.capture import (
    BackgroundVoiceReportSink,
    LoggingVoiceReportSink,
    TranscribingVoiceReportSink,
    VoiceReportSink,
)
from api.channels.ivr.flow import IvrFlow
from api.channels.ivr.sessions import get_ivr_session_store
from api.channels.ivr.signing import TwilioSignatureValidator
from api.core.app_state import AppState, get_app_state
from api.core.config import Settings, get_settings
from api.crises.routes import get_crisis_service
from api.crises.service import CrisisService

_logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ivr")

_TWIML_MEDIA_TYPE = "application/xml"


@lru_cache(maxsize=1)
def _get_validator() -> TwilioSignatureValidator:
    return TwilioSignatureValidator(get_settings().twilio_auth_token)


@lru_cache(maxsize=1)
def _get_logging_sink() -> LoggingVoiceReportSink:
    # Cached so its inspection ring survives across webhooks.
    return LoggingVoiceReportSink()


def _get_voice_sink(
    state: Annotated[AppState, Depends(get_app_state)],
    background_tasks: BackgroundTasks,
) -> VoiceReportSink:
    """Background transcribe-and-submit if ASR and Twilio are configured, else the logging sink."""
    settings = get_settings()
    if (
        state.ai_clients.transcription is not None
        and settings.twilio_account_sid
        and settings.twilio_auth_token
    ):
        inner = TranscribingVoiceReportSink(
            ai_clients=state.ai_clients,
            report_service=state.report_service,
            twilio_account_sid=settings.twilio_account_sid,
            twilio_auth_token=settings.twilio_auth_token,
        )
        return BackgroundVoiceReportSink(inner, background_tasks)
    return _get_logging_sink()


def _get_flow(crises: CrisisService, sink: VoiceReportSink) -> IvrFlow:
    return IvrFlow(sessions=get_ivr_session_store(), crises=crises, sink=sink)


def _require_configured(settings: Settings) -> None:
    if not (
        settings.twilio_account_sid and settings.twilio_auth_token and settings.twilio_voice_number
    ):
        raise HTTPException(status_code=503, detail="ivr_not_configured")


def _canonical_url(request: Request, settings: Settings) -> str:
    """The URL Twilio signed; behind a proxy request.url has the internal host."""
    if settings.ivr_public_url:
        base = settings.ivr_public_url.rstrip("/")
        query = request.url.query
        return f"{base}{request.url.path}{('?' + query) if query else ''}"
    return str(request.url)


async def _validated_form(
    request: Request,
    settings: Settings,
    signature: str | None,
) -> dict[str, str]:
    form_pairs = await request.form()
    form: dict[str, str] = {k: v for k, v in form_pairs.multi_items() if isinstance(v, str)}
    if signature is None:
        raise HTTPException(status_code=401, detail="missing_signature")
    url = _canonical_url(request, settings)
    if not _get_validator().validate(url, form, signature):
        _logger.warning("ivr.webhook.signature_invalid url=%s", url)
        raise HTTPException(status_code=401, detail="invalid_signature")
    return form


def _twiml(body: str) -> Response:
    return Response(content=body, media_type=_TWIML_MEDIA_TYPE)


def _int_or_none(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


@router.post("/voice")
async def ivr_voice(
    request: Request,
    crises: Annotated[CrisisService, Depends(get_crisis_service)],
    sink: Annotated[VoiceReportSink, Depends(_get_voice_sink)],
    x_twilio_signature: Annotated[str | None, Header(alias="X-Twilio-Signature")] = None,
) -> Response:
    settings = get_settings()
    _require_configured(settings)
    form = await _validated_form(request, settings, x_twilio_signature)
    call_sid = form.get("CallSid", "")
    from_e164 = form.get("From", "")
    if not call_sid:
        raise HTTPException(status_code=400, detail="missing_call_sid")
    twiml = await _get_flow(crises, sink).start(call_sid=call_sid, from_e164=from_e164)
    return _twiml(twiml)


@router.post("/input")
async def ivr_input(
    request: Request,
    crises: Annotated[CrisisService, Depends(get_crisis_service)],
    sink: Annotated[VoiceReportSink, Depends(_get_voice_sink)],
    x_twilio_signature: Annotated[str | None, Header(alias="X-Twilio-Signature")] = None,
) -> Response:
    settings = get_settings()
    _require_configured(settings)
    form = await _validated_form(request, settings, x_twilio_signature)
    call_sid = form.get("CallSid", "")
    if not call_sid:
        raise HTTPException(status_code=400, detail="missing_call_sid")
    twiml = await _get_flow(crises, sink).handle_input(
        call_sid=call_sid,
        digits=form.get("Digits") or None,
        recording_url=form.get("RecordingUrl") or None,
        recording_sid=form.get("RecordingSid") or None,
        recording_duration=_int_or_none(form.get("RecordingDuration")),
    )
    return _twiml(twiml)
