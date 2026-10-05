"""SMS template walker tests: description and route directions stand in for photo and pin."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from api.channels.sessions import InMemorySessionStore, LanguagePrefs, Session
from api.channels.sms.flow import SmsTemplateFlow
from api.channels.submitter import build_report_data
from api.crises.service import CrisisRow, CrisisService

# ---- fakes ---------------------------------------------------------------


class _RecordingSms:
    def __init__(self) -> None:
        self.texts: list[tuple[str, str]] = []

    async def send_text(self, to: str, body: str) -> None:
        self.texts.append((to, body))

    def last(self) -> str:
        return self.texts[-1][1] if self.texts else ""


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


class _FakeSubmitter:
    def __init__(self) -> None:
        self.submitted: list[Session] = []
        self.report_id = uuid.uuid4()
        self.should_raise = False

    async def submit(self, session: Session) -> uuid.UUID:
        if self.should_raise:
            raise RuntimeError("boom")
        self.submitted.append(replace(session))
        return self.report_id


CRISIS = CrisisRow(
    id=uuid.uuid4(), name="Aleppo earthquake", status="active", created_at=datetime.now(UTC)
)

# Full-parity schema: location + debris + infra_type + crisis_nature + one
# required generic. (description is collected unconditionally up front as the
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


def _make(
    *, form: tuple[dict[str, object], int] | None = FORM
) -> tuple[SmsTemplateFlow, _RecordingSms, _FakeSubmitter, InMemorySessionStore]:
    provider = _RecordingSms()
    sessions = InMemorySessionStore()
    crises = CrisisService(
        lookup=_StaticCrisisLookup([CRISIS], form=form),
        reserved_name="Other / Unspecified",
    )
    submitter = _FakeSubmitter()
    flow = SmsTemplateFlow(
        provider=provider,
        sessions=sessions,
        crises=crises,
        submitter=submitter,
        lang_prefs=LanguagePrefs(),
    )
    return flow, provider, submitter, sessions


FROM = "+97433885517"


async def _walk_to_review(flow: SmsTemplateFlow) -> None:
    """Drive a full report up to (but not including) the review answer."""
    await flow.handle(FROM, "hi")  # first contact -> LANGUAGE
    await flow.handle(FROM, "1")  # English -> CRISIS
    await flow.handle(FROM, "1")  # first crisis -> DAMAGE
    await flow.handle(FROM, "3")  # complete -> DESCRIPTION
    await flow.handle(FROM, "north wall collapsed")  # -> ROUTE (location slot)
    await flow.handle(FROM, "5th street, behind the school")  # -> DEBRIS
    await flow.handle(FROM, "1")  # debris yes -> INFRA_TYPE
    await flow.handle(FROM, "8")  # infra "other" -> INFRA_TYPE_OTHER
    await flow.handle(FROM, "grain silo")  # -> CRISIS_NATURE
    await flow.handle(FROM, "11")  # nature "Other" -> CRISIS_NATURE_OTHER
    await flow.handle(FROM, "meteor strike")  # -> GENERIC (Casualties?)
    await flow.handle(FROM, "2")  # "Some" -> REVIEW


@pytest.mark.asyncio
async def test_other_specs_accept_empty() -> None:
    # The free-text "Other" specs are optional (the PWA allows them blank) — an
    # empty reply advances rather than re-prompting.
    flow, _provider, _sub, sessions = _make()
    await flow.handle(FROM, "hi")
    await flow.handle(FROM, "1")  # English -> CRISIS
    await flow.handle(FROM, "1")  # first crisis -> DAMAGE
    await flow.handle(FROM, "3")  # complete -> DESCRIPTION
    await flow.handle(FROM, "north wall collapsed")  # -> ROUTE
    await flow.handle(FROM, "5th street")  # -> DEBRIS
    await flow.handle(FROM, "1")  # debris yes -> INFRA_TYPE
    await flow.handle(FROM, "8")  # infra "other" -> INFRA_TYPE_OTHER
    await flow.handle(FROM, "   ")  # empty spec -> CRISIS_NATURE
    s = await sessions.get(FROM)
    assert s is not None
    assert s.infra_type == ["other"]
    assert s.infra_type_other is None
    assert s.scratch["sms_step"] == "CRISIS_NATURE"

    await flow.handle(FROM, "11")  # nature "Other" -> CRISIS_NATURE_OTHER
    await flow.handle(FROM, "")  # empty spec -> GENERIC
    s = await sessions.get(FROM)
    assert s is not None
    assert s.crisis_nature == "Other"
    assert s.crisis_nature_other is None
    assert s.scratch["sms_step"] != "CRISIS_NATURE_OTHER"


@pytest.mark.asyncio
async def test_full_report_submits_and_clears_session() -> None:
    flow, provider, submitter, sessions = _make()
    await _walk_to_review(flow)
    await flow.handle(FROM, "1")  # submit

    assert len(submitter.submitted) == 1
    s = submitter.submitted[0]
    assert s.crisis_id == CRISIS.id
    assert s.damage_class == "complete"
    assert s.infra_description == "north wall collapsed"
    assert s.route_description == "5th street, behind the school"
    assert s.debris == "yes"
    assert s.infra_type == ["other"]
    assert s.infra_type_other == "grain silo"
    assert s.crisis_nature == "Other"
    assert s.crisis_nature_other == "meteor strike"
    assert s.generic_answers == {"Casualties?": "Some"}
    # Session deleted on success; a confirmation with the ref was sent.
    assert await sessions.get(FROM) is None
    assert str(submitter.report_id)[:8] in provider.last()


@pytest.mark.asyncio
async def test_optional_builtin_can_be_skipped_and_still_submits() -> None:
    # debris is enabled but marked required=false: the resident may reply "0" to
    # skip it, and the report still submits with debris unset.
    schema = {
        "pages": [
            {"kind": "photo_and_damage", "enabled": True},
            {"kind": "location", "enabled": True},
            {"kind": "debris", "enabled": True, "required": False},
            {"kind": "infra_type", "enabled": True},
            {"kind": "crisis_nature", "enabled": True},
        ]
    }
    flow, provider, submitter, _sessions = _make(form=(schema, 8))
    await flow.handle(FROM, "hi")  # -> LANGUAGE
    await flow.handle(FROM, "1")  # English -> CRISIS
    await flow.handle(FROM, "1")  # crisis -> DAMAGE
    await flow.handle(FROM, "2")  # partial -> DESCRIPTION
    await flow.handle(FROM, "cracked facade")  # -> ROUTE
    await flow.handle(FROM, "main road, third gate")  # -> DEBRIS
    # The debris prompt advertises the skip option.
    assert "0" in provider.last()
    await flow.handle(FROM, "0")  # skip debris -> INFRA_TYPE
    await flow.handle(FROM, "1")  # residential -> CRISIS_NATURE
    await flow.handle(FROM, "1")  # first nature -> REVIEW
    await flow.handle(FROM, "1")  # submit

    assert len(submitter.submitted) == 1
    s = submitter.submitted[0]
    assert s.debris is None  # skipped, did not block
    assert s.infra_type == ["residential"]


@pytest.mark.asyncio
async def test_first_contact_asks_language_then_crisis() -> None:
    flow, provider, _submitter, _sessions = _make()
    await flow.handle(FROM, "hello")
    assert "English" in provider.last() and "العربية" in provider.last()
    await flow.handle(FROM, "1")
    # Crisis prompt is numbered and lists the active crisis.
    assert "1. Aleppo earthquake" in provider.last()


@pytest.mark.asyncio
async def test_language_menu_lists_all_six_platform_languages() -> None:
    flow, provider, _submitter, _sessions = _make()
    await flow.handle(FROM, "hello")
    menu = provider.last()
    for native in ("English", "العربية", "Español", "Français", "Русский", "中文"):
        assert native in menu
    assert "6. 中文" in menu  # six numbered rows, zh last


@pytest.mark.asyncio
async def test_non_en_ar_language_localizes_prompts() -> None:
    flow, provider, _submitter, _sessions = _make()
    await flow.handle(FROM, "hello")
    await flow.handle(FROM, "3")  # Spanish (en, ar, es, ...) -> CRISIS in Spanish
    assert "¿Qué evento está reportando?" in provider.last()


@pytest.mark.asyncio
async def test_crisis_nature_options_localized_in_arabic() -> None:
    flow, provider, _submitter, _sessions = _make()
    await flow.handle(FROM, "hello")
    await flow.handle(FROM, "2")  # Arabic
    await flow.handle(FROM, "1")  # crisis -> DAMAGE
    await flow.handle(FROM, "3")  # complete -> DESCRIPTION
    await flow.handle(FROM, "نص الوصف")  # description -> ROUTE
    await flow.handle(FROM, "شارع رئيسي")  # route -> DEBRIS
    await flow.handle(FROM, "2")  # debris no -> INFRA_TYPE
    await flow.handle(FROM, "1")  # residential -> CRISIS_NATURE
    nature_menu = provider.last()
    assert "زلزال" in nature_menu  # Earthquake, localized
    assert "Earthquake" not in nature_menu  # canonical English not shown
    assert "أخرى" in nature_menu  # the trailing "Other" row, localized


@pytest.mark.asyncio
async def test_route_description_is_emitted_in_payload() -> None:
    # The submitter change: a session with route_description forwards it as the
    # location-or-route half of the minimum-content gate.
    session = Session(phone_e164=FROM)
    session.crisis_id = CRISIS.id
    session.damage_class = "partial"
    session.infra_description = "cracked facade"
    session.route_description = "Main St, opposite the clinic"
    data = build_report_data(session)
    assert data["route_description"] == "Main St, opposite the clinic"
    assert data["description"] == "cracked facade"


@pytest.mark.asyncio
async def test_cancel_at_review_terminates_without_submit() -> None:
    flow, provider, submitter, sessions = _make()
    await _walk_to_review(flow)
    await flow.handle(FROM, "3")  # cancel
    assert submitter.submitted == []
    assert await sessions.get(FROM) is None
    assert "cancel" in provider.last().lower()


@pytest.mark.asyncio
async def test_restart_midflow_resets() -> None:
    flow, provider, _submitter, sessions = _make()
    await flow.handle(FROM, "hi")  # LANGUAGE
    await flow.handle(FROM, "1")  # CRISIS
    await flow.handle(FROM, "1")  # DAMAGE
    await flow.handle(FROM, "restart")
    # Language is remembered, so restart jumps straight back to the crisis list.
    assert "1. Aleppo earthquake" in provider.last()
    session = await sessions.get(FROM)
    assert session is not None
    assert session.damage_class is None


@pytest.mark.asyncio
async def test_submit_failure_keeps_session_at_review() -> None:
    flow, provider, submitter, sessions = _make()
    submitter.should_raise = True
    await _walk_to_review(flow)
    await flow.handle(FROM, "1")  # submit -> fails
    # Session survives so the resident can retry without re-entering anything.
    session = await sessions.get(FROM)
    assert session is not None
    assert session.scratch.get("sms_step") == "REVIEW"
    assert "wrong" in provider.last().lower() or "try again" in provider.last().lower()


@pytest.mark.asyncio
async def test_language_is_remembered_across_per_request_flows() -> None:
    # The route builds a new flow per webhook.
    provider = _RecordingSms()
    sessions = InMemorySessionStore()
    lang_prefs = LanguagePrefs()
    crises = CrisisService(
        lookup=_StaticCrisisLookup([CRISIS], form=FORM), reserved_name="Other / Unspecified"
    )

    def fresh() -> SmsTemplateFlow:
        return SmsTemplateFlow(
            provider=provider,
            sessions=sessions,
            crises=crises,
            submitter=_FakeSubmitter(),
            lang_prefs=lang_prefs,
        )

    await fresh().handle(FROM, "hi")  # -> LANGUAGE
    await fresh().handle(FROM, "2")  # Arabic -> CRISIS
    await sessions.delete(FROM)  # conversation ends (expiry, submit, ...)
    await fresh().handle(FROM, "hi")  # new conversation
    session = await sessions.get(FROM)
    assert session is not None
    assert session.language == "ar"
    assert session.scratch.get("sms_step") == "CRISIS"
