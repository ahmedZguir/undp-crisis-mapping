"""``build_report_data`` and ``ServiceReportSubmitter``."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, cast

import pytest

from api.channels.sessions import Session
from api.channels.submitter import ServiceReportSubmitter, SubmissionError, build_report_data
from api.crises.default_form import default_form_schema_copy
from api.reports.service import ReportContentError
from api.schemas.reports import ReportSubmitPayload


def _session(**overrides: Any) -> Session:
    s = Session(phone_e164="+9665")
    s.crisis_id = uuid.uuid4()
    s.damage_class = "partial"
    s.debris = "no"
    s.infra_type = ["residential"]
    s.crisis_nature = "Earthquake"
    s.form_schema = default_form_schema_copy()
    s.form_version = 3
    for key, value in overrides.items():
        setattr(s, key, value)
    return s


def test_payload_carries_form_version_and_generic_answers() -> None:
    s = _session(generic_answers={"Household size?": "5", "Needs?": ["Water", "Food"]})
    data = build_report_data(s)
    assert data["form_version"] == 3
    assert data["generic_answers"] == {"Household size?": "5", "Needs?": ["Water", "Food"]}
    # Enabled built-ins still flow through.
    assert data["debris"] == "no"
    assert data["infra_type"] == ["residential"]
    assert data["crisis_type_detailed"] == "Earthquake"
    assert data["crisis_type"] == "natural_hazards"


def test_disabled_builtin_field_is_omitted() -> None:
    schema = default_form_schema_copy()
    for page in schema["pages"]:
        if page["kind"] == "debris":
            page["enabled"] = False
    s = _session(form_schema=schema)
    data = build_report_data(s)
    # debris is set on the session but its page is disabled → not submitted.
    assert "debris" not in data
    # Other enabled built-ins are unaffected.
    assert data["infra_type"] == ["residential"]
    assert data["crisis_type_detailed"] == "Earthquake"


def test_no_form_version_or_generic_answers_omits_them() -> None:
    s = _session(form_version=None, generic_answers={})
    data = build_report_data(s)
    assert "form_version" not in data
    assert "generic_answers" not in data


def test_core_fields_always_present() -> None:
    s = _session()
    data = build_report_data(s)
    assert data["crisis_id"] == str(s.crisis_id)
    assert data["damage_class"] == "partial"
    assert data["client_submission_id"] == str(s.client_submission_id)


def test_description_is_sent_under_description_key() -> None:
    # The session field is `infra_description`, but the API/DB consolidated it
    # into `description`. Sending the old key would be silently dropped by
    # Pydantic (regression guard for the photo-or-description gate).
    s = _session(infra_description="Three storeys collapsed.")
    data = build_report_data(s)
    assert data["description"] == "Three storeys collapsed."
    assert "infra_description" not in data


def test_description_omitted_when_its_page_is_disabled() -> None:
    schema = default_form_schema_copy()
    for page in schema["pages"]:
        if page["kind"] == "description":
            page["enabled"] = False
    s = _session(form_schema=schema, infra_description="set but page off")
    data = build_report_data(s)
    assert "description" not in data


def test_text_and_voice_channels_send_description_even_when_page_is_disabled() -> None:
    schema = default_form_schema_copy()
    for page in schema["pages"]:
        if page["kind"] == "description":
            page["enabled"] = False
    s = _session(form_schema=schema, infra_description="north wall collapsed")
    data = build_report_data(s, always_send_description=True)
    assert data["description"] == "north wall collapsed"


def test_sms_submitter_always_sends_description() -> None:
    from api.channels.sms.routes import _get_submitter  # pyright: ignore[reportPrivateUsage]

    submitter = _get_submitter(cast(Any, SimpleNamespace(report_service=_FakeService())))
    assert submitter._always_send_description is True  # pyright: ignore[reportPrivateUsage]


# ---- ServiceReportSubmitter ----


class _FakeService:
    def __init__(self, exc: Exception | None = None) -> None:
        self.calls: list[tuple[bytes | None, str | None, ReportSubmitPayload]] = []
        self._exc = exc

    async def submit(
        self, photo: bytes | None, mime: str | None, payload: ReportSubmitPayload
    ) -> SimpleNamespace:
        self.calls.append((photo, mime, payload))
        if self._exc is not None:
            raise self._exc
        return SimpleNamespace(id=REPORT_ID)


REPORT_ID = uuid.uuid4()


def _submitter(service: _FakeService) -> ServiceReportSubmitter:
    return ServiceReportSubmitter(cast(Any, service), twilio_account_sid="", twilio_auth_token="")


@pytest.mark.asyncio
async def test_service_submitter_calls_the_service_with_a_typed_payload() -> None:
    service = _FakeService()
    s = _session(
        infra_description="roof gone",
        route_description="behind the school",
        photo_bytes=b"jpeg",
        photo_mime="image/png",
    )
    assert await _submitter(service).submit(s) == REPORT_ID
    photo, mime, payload = service.calls[0]
    assert (photo, mime) == (b"jpeg", "image/png")
    assert isinstance(payload, ReportSubmitPayload)
    assert payload.crisis_id == s.crisis_id
    assert payload.description == "roof gone"
    assert payload.client_submission_id == s.client_submission_id


@pytest.mark.asyncio
async def test_service_submitter_maps_content_rejection_to_submission_error() -> None:
    service = _FakeService(exc=ReportContentError("photo_or_description_required"))
    with pytest.raises(SubmissionError, match="photo_or_description_required"):
        await _submitter(service).submit(_session(route_description="x"))


@pytest.mark.asyncio
async def test_service_submitter_rejects_invalid_payload_before_the_service() -> None:
    service = _FakeService()
    with pytest.raises(SubmissionError, match="invalid report payload"):
        await _submitter(service).submit(_session(damage_class="not-a-class"))
    assert service.calls == []
