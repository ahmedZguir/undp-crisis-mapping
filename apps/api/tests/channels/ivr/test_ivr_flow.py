"""IVR walker tests: TwiML per turn and the ``VoiceReportCapture`` handed to the sink."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from api.channels.ivr.capture import VoiceReportCapture
from api.channels.ivr.flow import IvrFlow
from api.channels.sessions import InMemorySessionStore
from api.crises.service import CrisisRow, CrisisService

# ---- fakes ---------------------------------------------------------------


class _RecordingSink:
    def __init__(self) -> None:
        self.captured: list[VoiceReportCapture] = []

    async def capture(self, payload: VoiceReportCapture) -> None:
        self.captured.append(payload)


class _ReportSink:
    """Sink that creates a report, like ``TranscribingVoiceReportSink``."""

    def __init__(self, report_id: uuid.UUID) -> None:
        self.report_id = report_id

    async def capture(self, payload: VoiceReportCapture) -> uuid.UUID:
        return self.report_id


class _FailingSink:
    async def capture(self, payload: VoiceReportCapture) -> None:
        raise RuntimeError("boom")


class _StaticCrisisLookup:
    def __init__(
        self,
        rows: list[CrisisRow],
        *,
        form: tuple[dict[str, object], int] | None = None,
    ) -> None:
        self._rows = rows
        self._form = form

    async def fetch_by_id(
        self, crisis_id: uuid.UUID, *, _session: object | None = None
    ) -> CrisisRow | None:
        return next((r for r in self._rows if r.id == crisis_id), None)

    async def fetch_active(self) -> list[CrisisRow]:
        return list(self._rows)

    async def fetch_form_by_id(self, crisis_id: uuid.UUID) -> tuple[dict[str, object], int] | None:
        return self._form


CRISIS = CrisisRow(
    id=uuid.uuid4(), name="Aleppo earthquake", status="active", created_at=datetime.now(UTC)
)

# Full-parity schema: location + debris + infra_type + crisis_nature + one
# required generic. (description is recorded unconditionally up front as the
# photo substitute, so it need not be a schema page.)
FORM: tuple[dict[str, object], int] = (
    {
        "pages": [
            {"kind": "photo_and_damage", "enabled": True},
            {"kind": "location", "enabled": True},
            {"kind": "debris", "enabled": True},
            {"kind": "infra_type", "enabled": True},
            {"kind": "crisis_nature", "enabled": True},
            {
                "kind": "generic",
                "enabled": True,
                "questions": [
                    {
                        "type": "single_select",
                        "label": "Casualties?",
                        "required": True,
                        "options": [{"label": "None"}, {"label": "Some"}],
                    }
                ],
            },
        ]
    },
    7,
)

CALL = "CA00000000000000000000000000000000"
FROM = "+15551234567"
DESC_URL = "https://api.twilio.com/2010-04-01/Recordings/RE_description"
LOC_URL = "https://api.twilio.com/2010-04-01/Recordings/RE_location"
INFRA_URL = "https://api.twilio.com/2010-04-01/Recordings/RE_infra_other"
NATURE_URL = "https://api.twilio.com/2010-04-01/Recordings/RE_nature_other"


def _make(
    *,
    form: tuple[dict[str, object], int] | None = FORM,
    rows: list[CrisisRow] | None = None,
    sink: object | None = None,
) -> tuple[IvrFlow, _RecordingSink, InMemorySessionStore]:
    sessions = InMemorySessionStore()
    crises = CrisisService(
        lookup=_StaticCrisisLookup([CRISIS] if rows is None else rows, form=form),
        reserved_name="Other / Unspecified",
    )
    rec_sink = sink if sink is not None else _RecordingSink()
    flow = IvrFlow(sessions=sessions, crises=crises, sink=rec_sink)  # pyright: ignore[reportArgumentType]
    return flow, rec_sink, sessions  # pyright: ignore[reportReturnType]


async def _press(flow: IvrFlow, digits: str) -> str:
    return await flow.handle_input(
        call_sid=CALL,
        digits=digits,
        recording_url=None,
        recording_sid=None,
        recording_duration=None,
    )


async def _record(flow: IvrFlow, url: str) -> str:
    return await flow.handle_input(
        call_sid=CALL, digits=None, recording_url=url, recording_sid="RE_sid", recording_duration=5
    )


async def _walk_to_review(flow: IvrFlow) -> None:
    """Drive a full report up to (but not including) the review answer."""
    await flow.start(call_sid=CALL, from_e164=FROM)  # -> LANGUAGE
    await _press(flow, "1")  # English -> CRISIS
    await _press(flow, "1")  # first crisis -> DAMAGE
    await _press(flow, "3")  # complete -> DESCRIPTION (record)
    await _record(flow, DESC_URL)  # description -> ROUTE (location slot, record)
    await _record(flow, LOC_URL)  # location -> DEBRIS
    await _press(flow, "1")  # debris yes -> INFRA_TYPE
    await _press(flow, "8")  # infra "other" -> INFRA_TYPE_OTHER (record)
    await _record(flow, INFRA_URL)  # -> CRISIS_NATURE
    await _press(flow, "11")  # nature "Other" (11th, >9 → finish-key) -> CRISIS_NATURE_OTHER
    await _record(flow, NATURE_URL)  # -> GENERIC (Casualties?)
    await _press(flow, "2")  # "Some" -> REVIEW


@pytest.mark.asyncio
async def test_other_specs_accept_silence() -> None:
    # The free-text "Other" specs are optional (the PWA allows them blank) — if
    # the caller stays silent (no recording), advance rather than re-prompting.
    flow, _sink, sessions = _make()
    await flow.start(call_sid=CALL, from_e164=FROM)
    await _press(flow, "1")  # English -> CRISIS
    await _press(flow, "1")  # first crisis -> DAMAGE
    await _press(flow, "3")  # complete -> DESCRIPTION
    await _record(flow, DESC_URL)  # -> ROUTE
    await _record(flow, LOC_URL)  # -> DEBRIS
    await _press(flow, "1")  # debris yes -> INFRA_TYPE
    await _press(flow, "8")  # infra "other" -> INFRA_TYPE_OTHER

    # Silence: no recording_url, no digits.
    await flow.handle_input(
        call_sid=CALL, digits=None, recording_url=None, recording_sid=None, recording_duration=None
    )
    s = await sessions.get(CALL)
    assert s is not None
    assert s.infra_type == ["other"]
    assert s.scratch["ivr_step"] == "CRISIS_NATURE"

    await _press(flow, "11")  # nature "Other" -> CRISIS_NATURE_OTHER
    await flow.handle_input(
        call_sid=CALL, digits=None, recording_url=None, recording_sid=None, recording_duration=None
    )
    s = await sessions.get(CALL)
    assert s is not None
    assert s.crisis_nature == "Other"
    assert s.scratch["ivr_step"] != "CRISIS_NATURE_OTHER"
    # No recording was stashed for either optional spec.
    recs = s.scratch.get("ivr_recordings", [])
    fields = [r["field"] for r in recs]
    assert "infra_type_other" not in fields
    assert "crisis_nature_other" not in fields


@pytest.mark.asyncio
async def test_full_report_captures_and_clears_session() -> None:
    flow, sink, sessions = _make()
    await _walk_to_review(flow)
    twiml = await _press(flow, "1")  # submit

    assert len(sink.captured) == 1
    cap = sink.captured[0]
    assert cap.call_sid == CALL
    assert cap.from_e164 == FROM
    assert cap.crisis_id == CRISIS.id
    assert cap.language == "en"
    assert cap.damage_class == "complete"
    assert cap.debris == "yes"
    assert cap.infra_type == ["other"]
    assert cap.crisis_nature == "Other"
    assert cap.crisis_nature_is_other is True
    assert cap.generic_answers == {"Casualties?": "Some"}
    assert cap.form_version == 7

    # One recording per free-text slot, in walk order, with the URLs we fed.
    assert [r.field for r in cap.recordings] == [
        "description",
        "route_description",
        "infra_type_other",
        "crisis_nature_other",
    ]
    assert [r.url for r in cap.recordings] == [DESC_URL, LOC_URL, INFRA_URL, NATURE_URL]

    # No report id from the sink, so no reference is read out.
    assert await sessions.get(CALL) is None
    assert "<Hangup" in twiml
    assert "has been received and will be processed" in twiml
    assert str(cap.client_submission_id)[:8] not in twiml
    assert cap.form_schema == FORM[0]


@pytest.mark.asyncio
async def test_optional_builtin_can_be_skipped_and_still_submits() -> None:
    # debris is enabled but marked required=false: the menu offers a skip key,
    # pressing 0 advances without setting debris, and the report still submits.
    schema: dict[str, object] = {
        "pages": [
            {"kind": "photo_and_damage", "enabled": True},
            {"kind": "location", "enabled": True},
            {"kind": "debris", "enabled": True, "required": False},
            {"kind": "infra_type", "enabled": True},
            {"kind": "crisis_nature", "enabled": True},
        ]
    }
    flow, sink, _sessions = _make(form=(schema, 8))
    await flow.start(call_sid=CALL, from_e164=FROM)  # -> LANGUAGE
    await _press(flow, "1")  # English -> CRISIS
    await _press(flow, "1")  # crisis -> DAMAGE
    await _press(flow, "2")  # partial -> DESCRIPTION (record)
    await _record(flow, DESC_URL)  # -> ROUTE
    debris_twiml = await _record(flow, LOC_URL)  # -> DEBRIS
    # The optional debris menu advertises the skip key.
    assert "press 0" in debris_twiml.lower()
    await _press(flow, "0")  # skip debris -> INFRA_TYPE
    await _press(flow, "1")  # residential -> CRISIS_NATURE
    await _press(flow, "1")  # first nature -> REVIEW
    await _press(flow, "1")  # submit

    assert len(sink.captured) == 1
    cap = sink.captured[0]
    assert cap.debris is None  # skipped, did not block submission
    assert cap.infra_type == ["residential"]


@pytest.mark.asyncio
async def test_start_greets_and_asks_language() -> None:
    flow, _sink, _sessions = _make()
    twiml = await flow.start(call_sid=CALL, from_e164=FROM)
    assert "<Gather" in twiml
    assert "RASID" in twiml  # the greeting intro
    assert "For English, press 1" in twiml


@pytest.mark.asyncio
async def test_language_menu_offers_six_languages_each_in_its_own_voice() -> None:
    flow, _sink, _sessions = _make()
    twiml = await flow.start(call_sid=CALL, from_e164=FROM)
    # Each option is read in its own language's prose + Polly voice tag.
    assert 'language="en-US"' in twiml and "For English, press 1" in twiml
    assert 'language="arb"' in twiml and "اضغط 2" in twiml
    assert 'language="es-ES"' in twiml and "Para español, pulse 3" in twiml
    assert 'language="fr-FR"' in twiml and "appuyez sur 4" in twiml
    assert 'language="ru-RU"' in twiml and "нажмите 5" in twiml
    assert 'language="zh-CN"' in twiml and "请按 6" in twiml


@pytest.mark.asyncio
async def test_non_en_ar_language_localizes_voice_prompts() -> None:
    flow, _sink, _sessions = _make()
    await flow.start(call_sid=CALL, from_e164=FROM)
    crisis_twiml = await _press(flow, "3")  # Spanish (en, ar, es, ...)
    assert "¿Qué evento está reportando?" in crisis_twiml
    assert 'language="es-ES"' in crisis_twiml


@pytest.mark.asyncio
async def test_crisis_and_damage_menus_are_spoken() -> None:
    flow, _sink, _sessions = _make()
    await flow.start(call_sid=CALL, from_e164=FROM)
    crisis_twiml = await _press(flow, "1")
    assert "Press 1 for Aleppo earthquake" in crisis_twiml
    damage_twiml = await _press(flow, "1")
    assert "Press 1 for minimal damage" in damage_twiml
    # Damage answered -> a <Record> prompt for the description (photo substitute).
    desc_twiml = await _press(flow, "3")
    assert "<Record" in desc_twiml


@pytest.mark.asyncio
async def test_invalid_keypress_reprompts_same_step() -> None:
    flow, _sink, sessions = _make()
    await flow.start(call_sid=CALL, from_e164=FROM)
    await _press(flow, "1")  # -> CRISIS
    await _press(flow, "1")  # -> DAMAGE
    twiml = await _press(flow, "9")  # invalid (only 1..3) -> re-prompt DAMAGE
    assert "How badly is the building damaged?" in twiml
    session = await sessions.get(CALL)
    assert session is not None
    assert session.scratch.get("ivr_step") == "DAMAGE"


@pytest.mark.asyncio
async def test_silent_recording_reprompts() -> None:
    flow, _sink, sessions = _make()
    await flow.start(call_sid=CALL, from_e164=FROM)
    await _press(flow, "1")  # CRISIS
    await _press(flow, "1")  # DAMAGE
    await _press(flow, "3")  # -> DESCRIPTION (record)
    # No recording arrived (silence) -> re-prompt the record step.
    twiml = await flow.handle_input(
        call_sid=CALL, digits=None, recording_url=None, recording_sid=None, recording_duration=None
    )
    assert "<Record" in twiml
    session = await sessions.get(CALL)
    assert session is not None
    assert session.scratch.get("ivr_step") == "DESCRIPTION"


@pytest.mark.asyncio
async def test_start_over_at_review_resets_draft() -> None:
    flow, sink, sessions = _make()
    await _walk_to_review(flow)
    twiml = await _press(flow, "2")  # start over
    assert sink.captured == []
    assert "For English, press 1" in twiml
    session = await sessions.get(CALL)
    assert session is not None
    assert session.damage_class is None
    assert session.crisis_id is None
    assert session.scratch.get("ivr_step") == "LANGUAGE"


@pytest.mark.asyncio
async def test_capture_failure_hangs_up_without_losing_face() -> None:
    flow, _sink, sessions = _make(sink=_FailingSink())
    await _walk_to_review(flow)
    twiml = await _press(flow, "1")  # submit -> sink raises
    assert "<Hangup" in twiml
    assert "wrong" in twiml.lower() or "later" in twiml.lower()
    assert await sessions.get(CALL) is None


@pytest.mark.asyncio
async def test_submitted_reference_is_the_report_id() -> None:
    report_id = uuid.uuid4()
    flow, _sink, _sessions = _make(sink=_ReportSink(report_id))
    await _walk_to_review(flow)
    twiml = await _press(flow, "1")  # submit
    assert str(report_id)[:8] in twiml


@pytest.mark.asyncio
async def test_start_over_drops_previous_crisis_form() -> None:
    # Crisis B has no form, so it must not inherit A's schema or form_version.
    lookup = _StaticCrisisLookup([CRISIS], form=FORM)
    sessions = InMemorySessionStore()
    crises = CrisisService(lookup=lookup, reserved_name="Other / Unspecified")
    flow = IvrFlow(sessions=sessions, crises=crises, sink=_RecordingSink())
    await _walk_to_review(flow)
    await _press(flow, "2")  # start over -> LANGUAGE

    lookup._form = None  # pyright: ignore[reportPrivateUsage]
    await _press(flow, "1")  # English -> CRISIS
    await _press(flow, "1")  # crisis with no form -> DAMAGE
    session = await sessions.get(CALL)
    assert session is not None
    assert session.form_schema is None
    assert session.form_version is None


@pytest.mark.asyncio
async def test_no_active_crises_hangs_up() -> None:
    flow, _sink, _sessions = _make(rows=[])
    await flow.start(call_sid=CALL, from_e164=FROM)
    twiml = await _press(flow, "1")  # pick language -> CRISIS, but none active
    assert "no active events" in twiml.lower()
    assert "<Hangup" in twiml


@pytest.mark.asyncio
async def test_multi_select_is_single_pick() -> None:
    # A generic multi_select is rendered as a single-pick menu (one keypress),
    # and the chosen option is stored as a plain string (not a list).
    form: tuple[dict[str, object], int] = (
        {
            "pages": [
                {"kind": "photo_and_damage", "enabled": True},
                {"kind": "location", "enabled": True},
                {
                    "kind": "generic",
                    "enabled": True,
                    "questions": [
                        {
                            "type": "multi_select",
                            "label": "Utilities affected?",
                            "required": True,
                            "options": [{"label": "Water"}, {"label": "Power"}, {"label": "Gas"}],
                        }
                    ],
                },
            ]
        },
        3,
    )
    flow, sink, sessions = _make(form=form)
    await flow.start(call_sid=CALL, from_e164=FROM)
    await _press(flow, "1")  # CRISIS
    await _press(flow, "1")  # DAMAGE
    await _press(flow, "2")  # partial -> DESCRIPTION
    await _record(flow, DESC_URL)  # -> ROUTE
    await _record(flow, LOC_URL)  # -> GENERIC (multi_select)
    session = await sessions.get(CALL)
    assert session is not None
    assert session.scratch.get("ivr_step") == "GENERIC"

    generic_twiml = await flow.handle_input(
        call_sid=CALL, digits=None, recording_url=None, recording_sid=None, recording_duration=None
    )
    # Rendered as a numbered single-pick menu, not a record.
    assert "Press 1 for Water" in generic_twiml
    assert "<Record" not in generic_twiml

    await _press(flow, "2")  # pick "Power" -> REVIEW
    await _press(flow, "1")  # submit
    cap = sink.captured[0]
    assert cap.generic_answers == {"Utilities affected?": "Power"}
