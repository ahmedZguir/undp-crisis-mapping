"""WhatsApp deterministic template walker tests."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

import pytest

from api.channels.sessions import InMemorySessionStore, LanguagePrefs, Session
from api.channels.whatsapp.adapter import Button, ListRow
from api.channels.whatsapp.flow import InboundMessage
from api.channels.whatsapp.template_flow import TemplateConversationFlow
from api.crises.service import CrisisRow, CrisisService

# ---- fakes ---------------------------------------------------------------


class _RecordingProvider:
    def __init__(self) -> None:
        self.texts: list[tuple[str, str]] = []
        self.lists: list[tuple[str, str, list[ListRow]]] = []
        self.buttons: list[tuple[str, str, list[Button]]] = []
        self.location_requests: list[tuple[str, str]] = []

    async def send_text(self, to: str, body: str) -> None:
        self.texts.append((to, body))

    async def send_list(self, to: str, body: str, button: str, rows: list[ListRow]) -> None:
        self.lists.append((to, body, rows))

    async def send_buttons(self, to: str, body: str, buttons: list[Button]) -> None:
        self.buttons.append((to, body, buttons))

    async def send_flow(self, *args: object, **kwargs: object) -> None:  # pragma: no cover
        raise NotImplementedError

    async def send_location_request(self, to: str, body: str) -> None:
        self.location_requests.append((to, body))


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
        self,
        crisis_id: uuid.UUID,
        *,
        _session: object | None = None,
    ) -> CrisisRow | None:
        for r in self._rows:
            if r.id == crisis_id:
                return r
        return None

    async def fetch_active(self) -> list[CrisisRow]:
        return list(self._rows)

    async def fetch_form_by_id(
        self,
        crisis_id: uuid.UUID,
    ) -> tuple[dict[str, object], int] | None:
        return self._form


class _FakeSubmitter:
    def __init__(self) -> None:
        self.submitted: list[Session] = []
        self.report_id = uuid.uuid4()
        self.should_raise = False

    async def submit(self, session: Session) -> uuid.UUID:
        if self.should_raise:
            raise RuntimeError("boom")
        # Snapshot the session so subsequent mutations in the test don't
        # bleed back into the recorded value.
        self.submitted.append(replace(session))
        return self.report_id


def _crisis(name: str) -> CrisisRow:
    return CrisisRow(id=uuid.uuid4(), name=name, status="active", created_at=datetime.now(UTC))


CRISIS = _crisis("Aleppo earthquake response")
RESERVED = CrisisRow(
    id=uuid.uuid4(),
    name="Other / Unspecified",
    status="active",
    created_at=datetime.now(UTC),
)


def _make(
    *,
    form: tuple[dict[str, object], int] | None = None,
    crises: list[CrisisRow] | None = None,
) -> tuple[
    TemplateConversationFlow,
    _RecordingProvider,
    _FakeSubmitter,
    InMemorySessionStore,
]:
    provider = _RecordingProvider()
    sessions = InMemorySessionStore()
    crisis_service = CrisisService(
        lookup=_StaticCrisisLookup(crises if crises is not None else [CRISIS, RESERVED], form=form),
        reserved_name="Other / Unspecified",
    )
    submitter = _FakeSubmitter()
    flow = TemplateConversationFlow(
        provider=provider,
        sessions=sessions,
        crises=crisis_service,
        submitter=submitter,
        lang_prefs=LanguagePrefs(),
    )
    return flow, provider, submitter, sessions


PHONE = "+9665"


def _msg(
    *,
    body: str = "",
    list_id: str | None = None,
    button: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    media_bytes: bytes | None = None,
    media_mime: str | None = None,
) -> InboundMessage:
    return InboundMessage(
        from_e164=PHONE,
        body=body,
        num_media=1 if media_bytes else 0,
        media_url_0=None,
        media_type_0=media_mime,
        latitude=lat,
        longitude=lng,
        button_payload=button,
        list_id=list_id,
        media_bytes=media_bytes,
        media_mime=media_mime,
    )


# ---- tests ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_contact_sends_language_list() -> None:
    flow, provider, _sub, sessions = _make()
    await flow.handle(_msg(body="hi"))

    # First contact: only the language picker, no intro/crisis yet.
    assert len(provider.lists) == 1
    rows = provider.lists[0][2]
    row_ids = [r.id for r in rows]
    assert "lang:en" in row_ids
    assert "lang:ar" in row_ids
    assert "lang:zh" in row_ids

    s = await sessions.get(PHONE)
    assert s is not None
    assert s.scratch["template_step"] == "LANGUAGE"
    assert s.scratch["origin"] == "template"


@pytest.mark.asyncio
async def test_language_pick_advances_to_crisis_and_is_remembered() -> None:
    flow, provider, _sub, sessions = _make()
    await flow.handle(_msg(body="hi"))
    provider.lists.clear()
    provider.texts.clear()

    await flow.handle(_msg(list_id="lang:fr"))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.language == "fr"
    assert s.scratch["template_step"] == "CRISIS"
    # Intro text in French + crisis list.
    assert len(provider.texts) == 1
    assert len(provider.lists) == 1

    # Simulate a returning user: clear the session (as if the previous
    # submission completed) and start over. Language must not be asked
    # again.
    await sessions.delete(PHONE)
    provider.lists.clear()
    provider.texts.clear()

    await flow.handle(_msg(body="hi"))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.language == "fr"
    assert s.scratch["template_step"] == "CRISIS"
    # Intro + crisis list, no language picker.
    assert all(not any(r.id.startswith("lang:") for r in rows) for _, _, rows in provider.lists)


@pytest.mark.asyncio
async def test_typed_crisis_name_is_rejected() -> None:
    flow, provider, _sub, sessions = _make()
    await flow.handle(_msg(body="hi"))
    await flow.handle(_msg(list_id="lang:en"))
    provider.lists.clear()
    provider.texts.clear()

    # Plain text — no list_id. Should re-send the list, not advance.
    await flow.handle(_msg(body="Aleppo earthquake response"))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.crisis_id is None
    assert s.scratch["template_step"] == "CRISIS"
    assert len(provider.lists) == 1


@pytest.mark.asyncio
async def test_many_crises_render_as_numbered_text() -> None:
    # More than a 10-row interactive list can hold (one row is reserved for
    # "Start over") → fall back to a numbered-text prompt listing every crisis,
    # with no truncation.
    many = [_crisis(f"Crisis {i}") for i in range(12)]
    flow, provider, _sub, sessions = _make(crises=many)
    await flow.handle(_msg(body="hi"))
    await flow.handle(_msg(list_id="lang:en"))

    s = await sessions.get(PHONE)
    assert s is not None
    assert s.scratch["template_step"] == "CRISIS"
    # Every crisis is offered — nothing dropped.
    assert len(s.active_crisis_choices) == len(many)
    # No interactive crisis list was sent; the prompt is plain numbered text.
    assert all(not any(r.id.startswith("crisis:") for r in rows) for _, _, rows in provider.lists)
    body = provider.texts[-1][1]
    for i, name in enumerate(s.active_crisis_choices):
        assert f"{i + 1}. {name}" in body


@pytest.mark.asyncio
async def test_many_crises_typed_number_selects_crisis() -> None:
    many = [_crisis(f"Crisis {i}") for i in range(12)]
    flow, _provider, _sub, sessions = _make(crises=many)
    await flow.handle(_msg(body="hi"))
    await flow.handle(_msg(list_id="lang:en"))

    s = await sessions.get(PHONE)
    assert s is not None
    ordered = list(s.active_crisis_choices.items())  # (name, cid)
    name, cid = ordered[2]  # the 3rd offered crisis

    await flow.handle(_msg(body="3"))
    s = await sessions.get(PHONE)
    assert s is not None
    assert str(s.crisis_id) == cid
    assert s.crisis_name == name
    assert s.scratch["template_step"] == "PHOTO"


@pytest.mark.asyncio
async def test_full_happy_path() -> None:
    flow, _provider, submitter, sessions = _make()

    # Step 0: greet + pick language.
    await flow.handle(_msg(body="hi"))
    await flow.handle(_msg(list_id="lang:en"))

    # Step 1: pick crisis.
    await flow.handle(_msg(list_id=f"crisis:{CRISIS.id}"))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.crisis_id == CRISIS.id
    assert s.scratch["template_step"] == "PHOTO"

    # Step 2: send photo.
    await flow.handle(_msg(media_bytes=b"\xff\xd8\xff", media_mime="image/jpeg"))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.photo_bytes == b"\xff\xd8\xff"
    assert s.scratch["template_step"] == "DAMAGE"

    # Step 3: damage. The default form pins location at index 1, so it comes
    # next — right after photo+damage — not at the very end.
    await flow.handle(_msg(button="damage:partial"))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.damage_class == "partial"
    assert s.scratch["template_step"] == "LOCATION"

    # Step 4: location (schema-ordered, before the other questions).
    await flow.handle(_msg(lat=36.2, lng=37.16))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.location == (36.2, 37.16)
    assert s.scratch["template_step"] == "DESCRIPTION"

    # Step 5: skip description.
    await flow.handle(_msg(button="desc:skip"))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.infra_description is None
    assert s.scratch["template_step"] == "DEBRIS"

    # Step 6: debris.
    await flow.handle(_msg(button="debris:no"))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.debris == "no"
    assert s.scratch["template_step"] == "INFRA_TYPE"

    # Step 7: infra type — single select, one tap advances.
    await flow.handle(_msg(list_id="infra:residential"))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.infra_type == ["residential"]
    assert s.scratch["template_step"] == "CRISIS_NATURE"

    # Step 8: nature — all options listed directly (numbered), reply with a
    # number. 1 == Earthquake (first label).
    await flow.handle(_msg(body="1"))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.crisis_nature == "Earthquake"
    assert s.scratch["template_step"] == "REVIEW"

    # Step 9: submit.
    await flow.handle(_msg(button="review:submit"))
    assert len(submitter.submitted) == 1
    assert await sessions.get(PHONE) is None


async def _walk_to_infra(flow: TemplateConversationFlow) -> None:
    """Default-form walk up to the INFRA_TYPE step (location comes first now)."""
    await flow.handle(_msg(body="hi"))
    await flow.handle(_msg(list_id="lang:en"))
    await flow.handle(_msg(list_id=f"crisis:{CRISIS.id}"))
    await flow.handle(_msg(media_bytes=b"\xff", media_mime="image/jpeg"))
    await flow.handle(_msg(button="damage:minimal"))
    await flow.handle(_msg(lat=36.2, lng=37.16))  # location (schema-ordered first)
    await flow.handle(_msg(button="desc:skip"))
    await flow.handle(_msg(button="debris:unknown"))


@pytest.mark.asyncio
async def test_infra_single_select_advances() -> None:
    # Single-select: one tap records a one-element list and advances straight
    # to the next step (no Done loop).
    flow, _provider, _sub, sessions = _make()
    await _walk_to_infra(flow)
    await flow.handle(_msg(list_id="infra:residential"))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.infra_type == ["residential"]
    assert s.scratch["template_step"] == "CRISIS_NATURE"


@pytest.mark.asyncio
async def test_infra_other_captures_free_text() -> None:
    flow, _provider, _sub, sessions = _make()
    await _walk_to_infra(flow)
    await flow.handle(_msg(list_id="infra:other"))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.scratch["template_step"] == "INFRA_TYPE_OTHER"

    await flow.handle(_msg(body="A small mosque"))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.infra_type_other == "A small mosque"
    assert s.scratch["template_step"] == "CRISIS_NATURE"


@pytest.mark.asyncio
async def test_infra_other_accepts_empty_spec() -> None:
    # The free-text spec is optional (the PWA allows it blank) — an empty reply
    # advances rather than re-prompting.
    flow, _provider, _sub, sessions = _make()
    await _walk_to_infra(flow)
    await flow.handle(_msg(list_id="infra:other"))
    await flow.handle(_msg(body="   "))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.infra_type == ["other"]
    assert s.infra_type_other is None
    assert s.scratch["template_step"] == "CRISIS_NATURE"


@pytest.mark.asyncio
async def test_nature_other_accepts_empty_spec() -> None:
    flow, _provider, _sub, sessions = _make()
    await _walk_to_infra(flow)
    await flow.handle(_msg(list_id="infra:residential"))
    # 11 == "Other" in the numbered nature prompt.
    await flow.handle(_msg(body="11"))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.scratch["template_step"] == "CRISIS_NATURE_OTHER"
    await flow.handle(_msg(body=""))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.crisis_nature == "Other"
    assert s.crisis_nature_other is None
    assert s.scratch["template_step"] == "REVIEW"


@pytest.mark.asyncio
async def test_nature_numbered_pick_records_label() -> None:
    flow, _provider, _sub, sessions = _make()
    await _walk_to_infra(flow)
    await flow.handle(_msg(list_id="infra:residential"))
    # Numbered nature list — reply with a number. 9 == Conflict.
    await flow.handle(_msg(body="9"))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.crisis_nature == "Conflict"
    assert s.scratch["template_step"] == "REVIEW"


@pytest.mark.asyncio
async def test_description_add_captures_text() -> None:
    flow, _provider, _sub, sessions = _make()
    await flow.handle(_msg(body="hi"))
    await flow.handle(_msg(list_id="lang:en"))
    await flow.handle(_msg(list_id=f"crisis:{CRISIS.id}"))
    await flow.handle(_msg(media_bytes=b"\xff", media_mime="image/jpeg"))
    await flow.handle(_msg(button="damage:complete"))
    # Location is asked before description in the default form.
    await flow.handle(_msg(lat=36.2, lng=37.16))
    await flow.handle(_msg(button="desc:add"))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.scratch["template_step"] == "DESCRIPTION_TEXT"

    await flow.handle(_msg(body="Three storeys collapsed."))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.infra_description == "Three storeys collapsed."
    assert s.scratch["template_step"] == "DEBRIS"


@pytest.mark.asyncio
async def test_cancel_terminates_without_submit() -> None:
    flow, _provider, submitter, sessions = _make()
    await _walk_to_review(flow)

    await flow.handle(_msg(button="review:cancel"))
    assert submitter.submitted == []
    assert await sessions.get(PHONE) is None


@pytest.mark.asyncio
async def test_reset_restarts() -> None:
    flow, provider, _sub, sessions = _make()
    await flow.handle(_msg(body="hi"))
    await flow.handle(_msg(list_id="lang:en"))
    await flow.handle(_msg(list_id=f"crisis:{CRISIS.id}"))
    s = await sessions.get(PHONE)
    assert s is not None and s.crisis_id == CRISIS.id

    await flow.handle(_msg(body="reset"))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.crisis_id is None
    # Language is remembered across reset — skip straight to CRISIS.
    assert s.language == "en"
    assert s.scratch["template_step"] == "CRISIS"
    # The reset path re-sends intro + list.
    assert any("crisis:" in r.id for _, _, rows in provider.lists for r in rows)


@pytest.mark.asyncio
async def test_submit_with_incomplete_draft_is_ignored() -> None:
    flow, _provider, submitter, sessions = _make()
    # Manually craft a session sitting on REVIEW with missing fields.
    session = Session(phone_e164=PHONE)
    session.scratch["template_step"] = "REVIEW"
    session.scratch["origin"] = "template"
    session.crisis_id = CRISIS.id
    session.crisis_name = CRISIS.name
    # No photo / damage / debris / infra_type / nature / location.
    await sessions.upsert(session)

    await flow.handle(_msg(button="review:submit"))
    assert submitter.submitted == []
    assert await sessions.get(PHONE) is not None


@pytest.mark.asyncio
async def test_submit_failure_keeps_session_alive() -> None:
    flow, _provider, submitter, sessions = _make()
    submitter.should_raise = True
    await _walk_to_review(flow)

    await flow.handle(_msg(button="review:submit"))
    # The session is still alive so the user can retry.
    assert await sessions.get(PHONE) is not None
    assert submitter.submitted == []


# ---- helpers -------------------------------------------------------------


async def _walk_to_review(flow: TemplateConversationFlow) -> None:
    await flow.handle(_msg(body="hi"))
    await flow.handle(_msg(list_id="lang:en"))
    await flow.handle(_msg(list_id=f"crisis:{CRISIS.id}"))
    await flow.handle(_msg(media_bytes=b"\xff", media_mime="image/jpeg"))
    await flow.handle(_msg(button="damage:partial"))
    await flow.handle(_msg(lat=36.2, lng=37.16))  # location is schema-ordered first
    await flow.handle(_msg(button="desc:skip"))
    await flow.handle(_msg(button="debris:no"))
    await flow.handle(_msg(list_id="infra:residential"))  # single-select
    await flow.handle(_msg(body="2"))  # nature (numbered): 2 == Flood


# ---- schema-driven generic questions -------------------------------------


def _schema(*generic_questions: dict[str, object], builtins: bool = False) -> dict[str, object]:
    """A form schema with the built-in middle pages optionally disabled and a
    single generic page appended (so the walk is short: photo → damage →
    generic(s) → location)."""
    pages: list[dict[str, object]] = [
        {"kind": "photo_and_damage", "enabled": True, "locked": True},
        {"kind": "location", "enabled": True, "locked": True},
        {"kind": "description", "enabled": builtins, "locked": False},
        {"kind": "debris", "enabled": builtins, "locked": False},
        {"kind": "infra_type", "enabled": builtins, "locked": False},
        {"kind": "crisis_nature", "enabled": builtins, "locked": False},
    ]
    if generic_questions:
        pages.append(
            {
                "kind": "generic",
                "enabled": True,
                "locked": False,
                "title": "Extra",
                "questions": list(generic_questions),
            }
        )
    return {"pages": pages}


def _has_generic_list(provider: _RecordingProvider) -> bool:
    """True if any interactive list rendered generic-question option rows."""
    return any(any(r.id.startswith("gen:") for r in rows) for _to, _body, rows in provider.lists)


async def _to_generic(flow: TemplateConversationFlow) -> None:
    """Drive a fresh session (built-ins disabled) up to the generic question.

    With ``_schema(builtins=False)`` the plan is [location, generic], so after
    photo+damage the location pin is captured first, then the generic is asked.
    """
    await flow.handle(_msg(body="hi"))
    await flow.handle(_msg(list_id="lang:en"))
    await flow.handle(_msg(list_id=f"crisis:{CRISIS.id}"))
    await flow.handle(_msg(media_bytes=b"\xff", media_mime="image/jpeg"))
    await flow.handle(_msg(button="damage:partial"))
    await flow.handle(_msg(lat=36.2, lng=37.16))  # location slot comes first


@pytest.mark.asyncio
async def test_generic_single_select_renders_list_and_records() -> None:
    schema = _schema(
        {
            "type": "single_select",
            "label": "Roof intact?",
            "required": True,
            "options": [{"label": "Yes"}, {"label": "No"}],
        },
    )
    flow, provider, submitter, sessions = _make(form=(schema, 4))
    await _to_generic(flow)
    # A list with gen:0 / gen:1 + restart (no done row for single-select).
    last_list = provider.lists[-1]
    row_ids = [r.id for r in last_list[2]]
    assert "gen:0" in row_ids and "gen:1" in row_ids
    assert "gen:done" not in row_ids

    await flow.handle(_msg(list_id="gen:1"))  # "No"
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.generic_answers == {"Roof intact?": "No"}
    # Location was captured first (slot 0); the generic is the last slot, so
    # answering it advances straight to REVIEW.
    assert s.scratch["template_step"] == "REVIEW"

    await flow.handle(_msg(button="review:submit"))
    assert len(submitter.submitted) == 1
    assert submitter.submitted[0].generic_answers == {"Roof intact?": "No"}
    assert submitter.submitted[0].form_version == 4


@pytest.mark.asyncio
async def test_generic_single_select_many_options_falls_back_to_numbered() -> None:
    options = [{"label": f"Option {i}"} for i in range(12)]
    schema = _schema(
        {"type": "single_select", "label": "Pick one", "required": True, "options": options},
    )
    flow, provider, _sub, sessions = _make(form=(schema, 1))
    await _to_generic(flow)
    # 12 options can't fit a 10-row list → numbered-text prompt, no gen: list.
    assert not _has_generic_list(provider)
    assert any("1. Option 0" in body for _to, body in provider.texts)

    await flow.handle(_msg(body="3"))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.generic_answers == {"Pick one": "Option 2"}


@pytest.mark.asyncio
async def test_generic_long_labels_fall_back_to_numbered() -> None:
    long = "A very long option label well beyond twenty-four characters"
    schema = _schema(
        {
            "type": "single_select",
            "label": "Choose",
            "required": True,
            "options": [{"label": long}, {"label": "Short"}],
        },
    )
    flow, provider, _sub, _sessions = _make(form=(schema, 1))
    await _to_generic(flow)
    # Two options, but one label > 24 chars → numbered fallback, no gen: list.
    assert not _has_generic_list(provider)
    assert any(long in body for _to, body in provider.texts)


@pytest.mark.asyncio
async def test_generic_multi_select_list_respects_max_select() -> None:
    schema = _schema(
        {
            "type": "multi_select",
            "label": "Needs?",
            "required": True,
            "max_select": 2,
            "options": [{"label": "Food"}, {"label": "Water"}, {"label": "Shelter"}],
        },
    )
    flow, provider, _sub, sessions = _make(form=(schema, 1))
    await _to_generic(flow)
    await flow.handle(_msg(list_id="gen:0"))  # Food
    await flow.handle(_msg(list_id="gen:1"))  # Water
    await flow.handle(_msg(list_id="gen:2"))  # Shelter → rejected (max 2)
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.generic_answers["Needs?"] == ["Food", "Water"]
    # A "max" message was surfaced.
    assert any("2" in body for _to, body in provider.texts)

    await flow.handle(_msg(list_id="gen:done"))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.scratch["template_step"] == "REVIEW"  # advanced past the generic


@pytest.mark.asyncio
async def test_generic_multi_select_numbered_parses_comma_list() -> None:
    long_opts = [{"label": f"A rather long need label number {i} here"} for i in range(3)]
    schema = _schema(
        {"type": "multi_select", "label": "Needs?", "required": True, "options": long_opts},
    )
    flow, _provider, _sub, sessions = _make(form=(schema, 1))
    await _to_generic(flow)
    await flow.handle(_msg(body="1, 3"))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.generic_answers["Needs?"] == [long_opts[0]["label"], long_opts[2]["label"]]


@pytest.mark.asyncio
async def test_generic_free_text_records_and_optional_skips() -> None:
    schema = _schema(
        {"type": "free_text", "label": "Notes?", "required": False},
    )
    flow, _provider, _sub, sessions = _make(form=(schema, 1))
    await _to_generic(flow)
    # Optional free-text: "0" skips without recording.
    await flow.handle(_msg(body="0"))
    s = await sessions.get(PHONE)
    assert s is not None
    assert "Notes?" not in s.generic_answers
    assert s.scratch["template_step"] == "REVIEW"  # advanced (location already done)


def _has_skip_button(provider: _RecordingProvider) -> bool:
    return any(any(b.id == "gen:skip" for b in btns) for _to, _body, btns in provider.buttons)


@pytest.mark.asyncio
async def test_optional_numbered_select_offers_tappable_skip_button() -> None:
    # Too many options for an interactive list → numbered-text fallback. The
    # optional skip must still be a tappable button, not a typed "0".
    options = [{"label": f"Option {i}"} for i in range(12)]
    schema = _schema(
        {"type": "single_select", "label": "Pick one", "required": False, "options": options},
    )
    flow, provider, _sub, sessions = _make(form=(schema, 1))
    await _to_generic(flow)
    assert not _has_generic_list(provider)  # rendered as numbered text
    assert _has_skip_button(provider)  # ...but skip is still a button

    # Tapping Skip advances without recording an answer.
    await flow.handle(_msg(button="gen:skip"))
    s = await sessions.get(PHONE)
    assert s is not None
    assert "Pick one" not in s.generic_answers
    assert s.scratch["template_step"] == "REVIEW"


@pytest.mark.asyncio
async def test_optional_free_text_offers_tappable_skip_button() -> None:
    schema = _schema({"type": "free_text", "label": "Notes?", "required": False})
    flow, provider, _sub, sessions = _make(form=(schema, 1))
    await _to_generic(flow)
    assert _has_skip_button(provider)

    await flow.handle(_msg(button="gen:skip"))
    s = await sessions.get(PHONE)
    assert s is not None
    assert "Notes?" not in s.generic_answers
    assert s.scratch["template_step"] == "REVIEW"


@pytest.mark.asyncio
async def test_required_generic_offers_no_skip_button() -> None:
    # A required question must not present a Skip affordance.
    options = [{"label": f"Option {i}"} for i in range(12)]
    schema = _schema(
        {"type": "single_select", "label": "Pick one", "required": True, "options": options},
    )
    flow, provider, _sub, _sessions = _make(form=(schema, 1))
    await _to_generic(flow)
    assert not _has_skip_button(provider)


@pytest.mark.asyncio
async def test_disabled_builtin_pages_are_skipped() -> None:
    # Default-style schema but with debris disabled and no generic page.
    schema = _schema(builtins=True)
    for page in cast(list[dict[str, object]], schema["pages"]):
        if page["kind"] == "debris":
            page["enabled"] = False
    flow, provider, submitter, _sessions = _make(form=(schema, 5))
    await flow.handle(_msg(body="hi"))
    await flow.handle(_msg(list_id="lang:en"))
    await flow.handle(_msg(list_id=f"crisis:{CRISIS.id}"))
    await flow.handle(_msg(media_bytes=b"\xff", media_mime="image/jpeg"))
    await flow.handle(_msg(button="damage:partial"))
    await flow.handle(_msg(lat=36.2, lng=37.16))  # location (schema-ordered first)
    await flow.handle(_msg(button="desc:skip"))
    # debris is disabled → the next step is INFRA_TYPE, not DEBRIS.
    # No debris button message should have been sent.
    debris_btns = [
        b for _to, _body, btns in provider.buttons for b in btns if b.id.startswith("debris:")
    ]
    assert debris_btns == []
    await flow.handle(_msg(list_id="infra:residential"))  # single-select
    await flow.handle(_msg(body="2"))  # nature (numbered): Flood
    await flow.handle(_msg(button="review:submit"))
    assert len(submitter.submitted) == 1
    assert submitter.submitted[0].debris is None


# ---- edit menu + restart hint --------------------------------------------


@pytest.mark.asyncio
async def test_review_edit_opens_menu_and_edits_single_step() -> None:
    flow, provider, submitter, sessions = _make()
    await _walk_to_review(flow)

    # Tapping Edit opens a picker of answers, not a full re-walk.
    await flow.handle(_msg(button="review:edit"))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.scratch["template_step"] == "EDIT_MENU"
    edit_rows = [r.id for r in provider.lists[-1][2]]
    assert "edit:damage" in edit_rows
    assert "edit:location" in edit_rows
    assert "edit:crisis_nature" in edit_rows

    # Pick "damage" → only the damage step is asked.
    await flow.handle(_msg(list_id="edit:damage"))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.scratch["template_step"] == "DAMAGE"

    # Answering it returns straight to REVIEW (not the next step) and other
    # answers are preserved.
    await flow.handle(_msg(button="damage:complete"))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.damage_class == "complete"
    assert s.crisis_nature == "Flood"  # untouched
    assert s.location == (36.2, 37.16)  # untouched
    assert s.scratch["template_step"] == "REVIEW"

    await flow.handle(_msg(button="review:submit"))
    assert len(submitter.submitted) == 1


@pytest.mark.asyncio
async def test_edit_generic_answer_returns_to_review() -> None:
    schema = _schema(
        {
            "type": "single_select",
            "label": "Roof intact?",
            "required": True,
            "options": [{"label": "Yes"}, {"label": "No"}],
        },
    )
    flow, provider, _sub, sessions = _make(form=(schema, 1))
    await _to_generic(flow)
    await flow.handle(_msg(list_id="gen:1"))  # "No" → REVIEW
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.scratch["template_step"] == "REVIEW"

    await flow.handle(_msg(button="review:edit"))
    edit_rows = [r.id for r in provider.lists[-1][2]]
    assert "edit:generic:1" in edit_rows  # location is slot 0, generic is slot 1

    await flow.handle(_msg(list_id="edit:generic:1"))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.scratch["template_step"] == "GENERIC"

    await flow.handle(_msg(list_id="gen:0"))  # change to "Yes"
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.generic_answers == {"Roof intact?": "Yes"}
    assert s.scratch["template_step"] == "REVIEW"


@pytest.mark.asyncio
async def test_steps_carry_a_restart_hint() -> None:
    flow, provider, _sub, _sessions = _make()
    await flow.handle(_msg(body="hi"))
    await flow.handle(_msg(list_id="lang:en"))
    await flow.handle(_msg(list_id=f"crisis:{CRISIS.id}"))
    # The photo prompt is a button step (default schema enables description, so
    # photo is optional with a describe-instead button) and carries the hint.
    assert any("restart" in body.lower() for _to, body, _btns in provider.buttons)
    # The damage prompt (another button step) carries it too.
    await flow.handle(_msg(media_bytes=b"\xff", media_mime="image/jpeg"))
    assert any("restart" in body.lower() for _to, body, _btns in provider.buttons)


@pytest.mark.asyncio
async def test_first_contact_then_greeting_after_language() -> None:
    flow, provider, _sub, _sessions = _make()
    # First contact: language picker only, no greeting yet.
    await flow.handle(_msg(body="hi"))
    assert provider.texts == []
    assert len(provider.lists) == 1

    # After picking language: a greeting message is sent (then the crisis list).
    await flow.handle(_msg(list_id="lang:en"))
    assert any("report building damage" in body.lower() for _to, body in provider.texts)


# ---- photo OR description (minimum-content gate) -------------------------


@pytest.mark.asyncio
async def test_photo_step_offers_describe_instead_when_description_enabled() -> None:
    # Default schema enables the description page, so the photo is optional.
    flow, provider, _sub, _sessions = _make()
    await flow.handle(_msg(body="hi"))
    await flow.handle(_msg(list_id="lang:en"))
    await flow.handle(_msg(list_id=f"crisis:{CRISIS.id}"))
    # The photo prompt is a button step carrying the describe-instead escape.
    assert any(any(b.id == "photo:skip" for b in btns) for _to, _body, btns in provider.buttons)


@pytest.mark.asyncio
async def test_skip_photo_prompts_description_then_damage_and_submits() -> None:
    # builtins enabled → walk is [location, description, debris, infra, nature].
    flow, _provider, submitter, sessions = _make(form=(_schema(builtins=True), 1))
    await flow.handle(_msg(body="hi"))
    await flow.handle(_msg(list_id="lang:en"))
    await flow.handle(_msg(list_id=f"crisis:{CRISIS.id}"))

    # Tapping "No photo — describe" asks for the description immediately (not
    # later), with no photo recorded.
    await flow.handle(_msg(button="photo:skip"))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.photo_bytes is None and s.photo_media_url is None
    assert s.scratch["template_step"] == "DESCRIPTION_TEXT"

    # After the description the walk picks up the fixed damage step.
    await flow.handle(_msg(body="Front wall collapsed, roof caved in."))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.infra_description == "Front wall collapsed, roof caved in."
    assert s.scratch["template_step"] == "DAMAGE"

    await flow.handle(_msg(button="damage:partial"))
    await flow.handle(_msg(lat=36.2, lng=37.16))  # location slot is first
    # The description slot is NOT asked again — it was captured up front, so the
    # walk advances straight to debris.
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.scratch["template_step"] == "DEBRIS"

    # Finish the remaining built-ins and submit a photo-less, described report.
    await flow.handle(_msg(button="debris:no"))
    await flow.handle(_msg(list_id="infra:residential"))
    await flow.handle(_msg(body="2"))  # nature: Flood
    await flow.handle(_msg(button="review:submit"))
    assert len(submitter.submitted) == 1
    submitted = submitter.submitted[0]
    assert submitted.photo_bytes is None
    assert submitted.infra_description == "Front wall collapsed, roof caved in."


@pytest.mark.asyncio
async def test_skip_photo_button_title_is_not_stored_as_description() -> None:
    # Real Meta button replies carry the option's title in `body` alongside the
    # id. Tapping "No photo — describe" must NOT store that label as the
    # description — it must route to the text-entry step and leave the field unset.
    flow, _provider, _sub, sessions = _make(form=(_schema(builtins=True), 1))
    await flow.handle(_msg(body="hi"))
    await flow.handle(_msg(list_id="lang:en"))
    await flow.handle(_msg(list_id=f"crisis:{CRISIS.id}"))

    await flow.handle(_msg(button="photo:skip", body="No photo, describe"))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.infra_description is None
    assert s.scratch["template_step"] == "DESCRIPTION_TEXT"


@pytest.mark.asyncio
async def test_extra_images_do_not_repeat_damage_prompt() -> None:
    # A multi-image send arrives as one webhook (one handle call) per image. The
    # first photo is captured and advances to DAMAGE; the extras land on DAMAGE
    # and must be dropped, not re-ask the damage question once per image.
    flow, provider, _sub, sessions = _make()
    await flow.handle(_msg(body="hi"))
    await flow.handle(_msg(list_id="lang:en"))
    await flow.handle(_msg(list_id=f"crisis:{CRISIS.id}"))

    await flow.handle(_msg(media_bytes=b"\x01", media_mime="image/jpeg"))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.scratch["template_step"] == "DAMAGE"
    assert s.photo_bytes == b"\x01"

    # Two more images from the same send arrive while on DAMAGE.
    await flow.handle(_msg(media_bytes=b"\x02", media_mime="image/jpeg"))
    await flow.handle(_msg(media_bytes=b"\x03", media_mime="image/jpeg"))

    s = await sessions.get(PHONE)
    assert s is not None
    # Still on DAMAGE, and only the first photo is kept.
    assert s.scratch["template_step"] == "DAMAGE"
    assert s.photo_bytes == b"\x01"
    # Exactly one damage-classification prompt was ever sent.
    damage_prompts = [
        btns
        for _to, _body, btns in provider.buttons
        if any(b.id.startswith("damage:") for b in btns)
    ]
    assert len(damage_prompts) == 1


@pytest.mark.asyncio
async def test_photo_stays_required_when_description_disabled() -> None:
    # No description page → no fallback over WhatsApp, so photo stays required:
    # the prompt is a plain text re-prompt with no describe-instead button, and
    # a "skip" button is ignored.
    flow, provider, _sub, sessions = _make(form=(_schema(builtins=False), 1))
    await flow.handle(_msg(body="hi"))
    await flow.handle(_msg(list_id="lang:en"))
    await flow.handle(_msg(list_id=f"crisis:{CRISIS.id}"))
    assert not any(any(b.id == "photo:skip" for b in btns) for _to, _body, btns in provider.buttons)
    # A stray skip payload does not advance — still on PHOTO.
    await flow.handle(_msg(button="photo:skip"))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.scratch["template_step"] == "PHOTO"


# ---- location OR route description (minimum-content gate) -----------------


@pytest.mark.asyncio
async def test_location_step_offers_type_directions_button() -> None:
    flow, provider, _sub, _sessions = _make()
    await flow.handle(_msg(body="hi"))
    await flow.handle(_msg(list_id="lang:en"))
    await flow.handle(_msg(list_id=f"crisis:{CRISIS.id}"))
    await flow.handle(_msg(media_bytes=b"\xff", media_mime="image/jpeg"))
    await flow.handle(_msg(button="damage:partial"))  # location is next
    # A location request plus a buttons message with the directions escape.
    assert any(any(b.id == "route:type" for b in btns) for _to, _body, btns in provider.buttons)


@pytest.mark.asyncio
async def test_typed_directions_satisfy_location_and_submit() -> None:
    flow, _provider, submitter, sessions = _make()
    await flow.handle(_msg(body="hi"))
    await flow.handle(_msg(list_id="lang:en"))
    await flow.handle(_msg(list_id=f"crisis:{CRISIS.id}"))
    await flow.handle(_msg(media_bytes=b"\xff", media_mime="image/jpeg"))
    await flow.handle(_msg(button="damage:partial"))

    # Decline the pin → directions text entry.
    await flow.handle(_msg(button="route:type"))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.scratch["template_step"] == "ROUTE"

    await flow.handle(_msg(body="Behind the old market, blue gate."))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.route_description == "Behind the old market, blue gate."
    assert s.location is None

    # Finish the remaining built-ins (default order after location).
    await flow.handle(_msg(button="desc:skip"))
    await flow.handle(_msg(button="debris:no"))
    await flow.handle(_msg(list_id="infra:residential"))
    await flow.handle(_msg(body="2"))  # nature: Flood
    await flow.handle(_msg(button="review:submit"))
    assert len(submitter.submitted) == 1
    submitted = submitter.submitted[0]
    assert submitted.location is None
    assert submitted.route_description == "Behind the old market, blue gate."


@pytest.mark.asyncio
async def test_type_directions_button_title_is_not_stored_as_route() -> None:
    # As with the photo step, the "Type directions" button reply carries its
    # title in `body`. Tapping it must route to the directions text-entry step
    # and NOT store the label as route_description.
    flow, _provider, _sub, sessions = _make()
    await flow.handle(_msg(body="hi"))
    await flow.handle(_msg(list_id="lang:en"))
    await flow.handle(_msg(list_id=f"crisis:{CRISIS.id}"))
    await flow.handle(_msg(media_bytes=b"\xff", media_mime="image/jpeg"))
    await flow.handle(_msg(button="damage:partial"))

    await flow.handle(_msg(button="route:type", body="Type directions"))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.route_description is None
    assert s.location is None
    assert s.scratch["template_step"] == "ROUTE"


@pytest.mark.asyncio
async def test_pin_still_advances_past_location() -> None:
    flow, _provider, _sub, sessions = _make()
    await flow.handle(_msg(body="hi"))
    await flow.handle(_msg(list_id="lang:en"))
    await flow.handle(_msg(list_id=f"crisis:{CRISIS.id}"))
    await flow.handle(_msg(media_bytes=b"\xff", media_mime="image/jpeg"))
    await flow.handle(_msg(button="damage:partial"))
    await flow.handle(_msg(lat=36.2, lng=37.16))
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.location == (36.2, 37.16)
    assert s.route_description is None
    assert s.scratch["template_step"] == "DESCRIPTION"


@pytest.mark.asyncio
async def test_language_is_remembered_across_per_request_flows() -> None:
    # The route builds a new flow per webhook.
    provider = _RecordingProvider()
    sessions = InMemorySessionStore()
    lang_prefs = LanguagePrefs()
    crises = CrisisService(
        lookup=_StaticCrisisLookup([CRISIS, RESERVED]), reserved_name="Other / Unspecified"
    )

    def fresh() -> TemplateConversationFlow:
        return TemplateConversationFlow(
            provider=provider,
            sessions=sessions,
            crises=crises,
            submitter=_FakeSubmitter(),
            lang_prefs=lang_prefs,
        )

    await fresh().handle(_msg(body="hi"))  # -> LANGUAGE
    await fresh().handle(_msg(list_id="lang:fr"))  # -> CRISIS
    await sessions.delete(PHONE)  # conversation ends
    await fresh().handle(_msg(body="hi"))  # new conversation
    s = await sessions.get(PHONE)
    assert s is not None
    assert s.language == "fr"
    assert s.scratch["template_step"] == "CRISIS"
