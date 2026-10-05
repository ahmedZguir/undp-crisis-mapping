"""IVR voice report sinks."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from fastapi import BackgroundTasks

from api.channels.ivr.capture import (
    BackgroundVoiceReportSink,
    TranscribingVoiceReportSink,
    VoiceReportCapture,
)
from api.channels.sessions import Session
from api.channels.submitter import SubmissionError


class _OkSubmitter:
    def __init__(self, report_id: uuid.UUID) -> None:
        self.report_id = report_id
        self.sessions: list[Session] = []

    async def submit(self, session: Session) -> uuid.UUID:
        self.sessions.append(session)
        return self.report_id


class _FailingSubmitter:
    async def submit(self, session: Session) -> uuid.UUID:
        raise SubmissionError("POST /reports -> 422")


def _sink(submitter: object) -> TranscribingVoiceReportSink:
    sink = TranscribingVoiceReportSink(
        ai_clients=cast(Any, object()),
        report_service=cast(Any, object()),
        twilio_account_sid="AC_test",
        twilio_auth_token="token",
    )
    sink._submitter = submitter  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]
    return sink


def _capture(**overrides: Any) -> VoiceReportCapture:
    capture = VoiceReportCapture(
        call_sid="CA_test",
        from_e164="+15551234567",
        crisis_id=uuid.uuid4(),
        captured_at=datetime.now(UTC),
        client_submission_id=uuid.uuid4(),
        damage_class="partial",
    )
    for key, value in overrides.items():
        setattr(capture, key, value)
    return capture


@pytest.mark.asyncio
async def test_capture_returns_report_id() -> None:
    report_id = uuid.uuid4()
    assert await _sink(_OkSubmitter(report_id)).capture(_capture()) == report_id


@pytest.mark.asyncio
async def test_capture_reraises_submission_failure() -> None:
    with pytest.raises(SubmissionError):
        await _sink(_FailingSubmitter()).capture(_capture())


@pytest.mark.asyncio
async def test_capture_submits_against_the_crisis_form() -> None:
    schema: dict[str, object] = {"pages": [{"kind": "debris", "enabled": False}]}
    submitter = _OkSubmitter(uuid.uuid4())
    await _sink(submitter).capture(_capture(form_schema=schema, form_version=4))
    assert submitter.sessions[0].form_schema == schema
    assert submitter.sessions[0].form_version == 4


def test_ivr_submitter_always_sends_description() -> None:
    sink = TranscribingVoiceReportSink(
        ai_clients=cast(Any, object()),
        report_service=cast(Any, object()),
        twilio_account_sid="AC_test",
        twilio_auth_token="token",
    )
    submitter = sink._submitter  # pyright: ignore[reportPrivateUsage]
    assert submitter._always_send_description is True  # pyright: ignore[reportPrivateUsage]


class _CountingSink:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self._fail = fail

    async def capture(self, payload: VoiceReportCapture) -> uuid.UUID:
        self.calls += 1
        if self._fail:
            raise SubmissionError("boom")
        return uuid.uuid4()


@pytest.mark.asyncio
async def test_background_sink_defers_work_until_after_the_response() -> None:
    inner = _CountingSink()
    tasks = BackgroundTasks()
    assert await BackgroundVoiceReportSink(inner, tasks).capture(_capture()) is None
    assert inner.calls == 0  # nothing ran inside the webhook
    await tasks()  # what Starlette does after sending the response
    assert inner.calls == 1


@pytest.mark.asyncio
async def test_background_sink_swallows_inner_failure() -> None:
    inner = _CountingSink(fail=True)
    tasks = BackgroundTasks()
    await BackgroundVoiceReportSink(inner, tasks).capture(_capture())
    await tasks()  # must not raise
    assert inner.calls == 1
