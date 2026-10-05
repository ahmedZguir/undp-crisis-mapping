"""Handoff from a finished IVR call (DTMF answers plus recordings) to report creation."""

from __future__ import annotations

import logging
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol

import httpx
from fastapi import BackgroundTasks

from api.ai.errors import AIClientUnavailableError
from api.ai.transcription import transcribe_audio
from api.channels.sessions import Session
from api.channels.submitter import ServiceReportSubmitter
from api.reports.audio import AudioValidationError, transcode_to_wav_async

if TYPE_CHECKING:
    from api.ai.client import AIClients
    from api.reports.service import ReportSubmissionService

_logger = logging.getLogger(__name__)


RecordingField = Literal[
    "description",
    "route_description",
    "generic_answer",
    "infra_type_other",
    "crisis_nature_other",
]


@dataclass(frozen=True)
class Recording:
    """``slot_target`` keys a generic_answer; ``url`` needs Twilio basic auth."""

    field: RecordingField
    slot_target: str
    url: str
    sid: str | None = None
    duration_seconds: int | None = None


def _empty_generic_answers() -> dict[str, str]:
    return {}


def _empty_recordings() -> list[Recording]:
    return []


@dataclass
class VoiceReportCapture:
    call_sid: str
    from_e164: str
    crisis_id: uuid.UUID
    captured_at: datetime
    # Per-call idempotency key; the report writer dedups on it.
    client_submission_id: uuid.UUID
    language: str | None = None
    damage_class: str | None = None
    debris: str | None = None
    # Single pick over voice, stored as a one-element list to match reports.infra_type.
    infra_type: list[str] | None = None
    crisis_nature: str | None = None
    crisis_nature_is_other: bool = False
    # Select answers keyed by question label; free-text generics arrive as recordings.
    generic_answers: dict[str, str] = field(default_factory=_empty_generic_answers)
    form_schema: dict[str, Any] | None = None
    form_version: int | None = None
    recordings: list[Recording] = field(default_factory=_empty_recordings)


class VoiceReportSink(Protocol):
    async def capture(self, payload: VoiceReportCapture) -> uuid.UUID | None:
        """The created report's id, or None if this sink creates none. Raises on failure."""
        ...


class LoggingVoiceReportSink:
    """Logs the capture and keeps a bounded ring for dev inspection."""

    def __init__(self, keep: int = 50) -> None:
        self.recent: deque[VoiceReportCapture] = deque(maxlen=keep)

    async def capture(self, payload: VoiceReportCapture) -> uuid.UUID | None:
        self.recent.append(payload)
        _logger.info(
            "ivr.capture call_sid=%s from=%s crisis=%s damage=%s "
            "debris=%s infra_type=%s nature=%s generics=%s recordings=%s",
            payload.call_sid,
            payload.from_e164,
            payload.crisis_id,
            payload.damage_class,
            payload.debris,
            payload.infra_type,
            payload.crisis_nature,
            dict(payload.generic_answers),
            [
                {"field": r.field, "target": r.slot_target, "url": r.url, "dur": r.duration_seconds}
                for r in payload.recordings
            ],
        )
        return None


class TranscribingVoiceReportSink:
    """Transcribe each recording, then submit.

    A failed transcription is skipped. IVR carries no photo, so a failed
    description transcription fails the photo-or-description gate and no report
    is created. A failed submission is re-raised so the caller isn't told
    "submitted".
    """

    def __init__(
        self,
        *,
        ai_clients: AIClients,
        report_service: ReportSubmissionService,
        twilio_account_sid: str,
        twilio_auth_token: str,
    ) -> None:
        self._ai_clients = ai_clients
        self._submitter = ServiceReportSubmitter(
            report_service,
            twilio_account_sid=twilio_account_sid,
            twilio_auth_token=twilio_auth_token,
            always_send_description=True,
        )
        self._account_sid = twilio_account_sid
        self._auth_token = twilio_auth_token

    async def _download_recording(self, url: str) -> bytes:
        mp3_url = url.rstrip("/") + ".mp3"
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(
                mp3_url,
                auth=(self._account_sid, self._auth_token),
                follow_redirects=True,
            )
            response.raise_for_status()
            return response.content

    async def _transcribe(self, recording: Recording, language: str | None) -> str | None:
        try:
            audio_bytes = await self._download_recording(recording.url)
            wav_bytes = await transcode_to_wav_async(audio_bytes)
            result = await transcribe_audio(
                wav_bytes,
                content_type="audio/wav",
                clients=self._ai_clients,
                language_hint=language,
            )
            return result.text or None
        except (AIClientUnavailableError, AudioValidationError) as exc:
            _logger.warning(
                "ivr.sink.transcription_unavailable field=%s sid=%s: %s",
                recording.field,
                recording.sid,
                exc,
            )
            return None
        except Exception:
            _logger.exception(
                "ivr.sink.transcription_failed field=%s sid=%s",
                recording.field,
                recording.sid,
            )
            return None

    async def capture(self, payload: VoiceReportCapture) -> uuid.UUID:
        session = Session(phone_e164=payload.from_e164)
        session.client_submission_id = payload.client_submission_id
        session.crisis_id = payload.crisis_id
        session.language = payload.language
        session.damage_class = payload.damage_class  # pyright: ignore[reportAttributeAccessIssue]
        session.debris = payload.debris  # pyright: ignore[reportAttributeAccessIssue]
        session.infra_type = list(payload.infra_type) if payload.infra_type else None
        session.crisis_nature = payload.crisis_nature
        session.generic_answers = dict(payload.generic_answers)
        session.form_schema = payload.form_schema
        session.form_version = payload.form_version

        # build_report_data appends the free-text suffix for "Other".
        if payload.crisis_nature_is_other:
            session.crisis_nature = "Other"

        for recording in payload.recordings:
            text = await self._transcribe(recording, payload.language)
            if text is None:
                continue
            if recording.field == "description":
                session.infra_description = text
            elif recording.field == "route_description":
                session.route_description = text
            elif recording.field == "generic_answer":
                session.generic_answers[recording.slot_target] = text
            elif recording.field == "infra_type_other":
                session.infra_type_other = text
            elif recording.field == "crisis_nature_other":
                session.crisis_nature_other = text

        try:
            report_id = await self._submitter.submit(session)
            _logger.info(
                "ivr.sink.submitted report=%s call=%s from=%s",
                report_id,
                payload.call_sid,
                payload.from_e164,
            )
        except Exception:
            _logger.exception(
                "ivr.sink.submission_failed call=%s from=%s",
                payload.call_sid,
                payload.from_e164,
            )
            raise
        return report_id


class BackgroundVoiceReportSink:
    """Runs ``inner.capture`` after the TwiML response, as it can outlast Twilio's 15 s timeout."""

    def __init__(self, inner: VoiceReportSink, background_tasks: BackgroundTasks) -> None:
        self._inner = inner
        self._background_tasks = background_tasks

    async def capture(self, payload: VoiceReportCapture) -> uuid.UUID | None:
        self._background_tasks.add_task(self._run, payload)
        return None

    async def _run(self, payload: VoiceReportCapture) -> None:
        try:
            await self._inner.capture(payload)
        except Exception:
            # Already logged by the inner sink.
            return
