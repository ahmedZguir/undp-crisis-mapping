"""LLM-driven WhatsApp flow: the LLM owns the conversation, the bot owns plumbing.

Invariants: no canned greeting before the LLM; ``set_crisis`` only accepts
names from the frozen snapshot; pins and photos are captured regardless of
state; ``submit_report`` is rejected while required fields are missing.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from api.channels.sessions import InMemorySessionStore, Session
from api.channels.whatsapp.adapter import Button, ListRow
from api.channels.whatsapp.flow import ConversationFlow, InboundMessage
from api.channels.whatsapp.llm import ToolCall, TurnResult
from api.channels.whatsapp.messages import strings_for
from api.crises.default_form import default_form_schema_copy
from api.crises.service import CrisisRow, CrisisService


class _RecordingProvider:
    def __init__(self) -> None:
        self.texts: list[tuple[str, str]] = []
        self.lists: list[tuple[str, str, list[ListRow]]] = []
        self.buttons: list[tuple[str, str, list[Button]]] = []

    async def send_text(self, to: str, body: str) -> None:
        self.texts.append((to, body))

    async def send_list(self, to: str, body: str, button: str, rows: list[ListRow]) -> None:
        self.lists.append((to, body, rows))

    async def send_buttons(self, to: str, body: str, buttons: list[Button]) -> None:
        self.buttons.append((to, body, buttons))

    def validate_signature(self, url: str, params: dict[str, str], signature: str) -> bool:
        return True


class _ScriptedLLM:
    def __init__(self, scripted: list[TurnResult] | None = None) -> None:
        self._scripted = list(scripted or [])
        self.calls: list[Session] = []

    async def run_turn(self, session: Session) -> TurnResult:
        self.calls.append(session)
        if not self._scripted:
            return TurnResult(reply_text="", tool_calls=[])
        return self._scripted.pop(0)


class _PromptCapturingLLM:
    """Fake LLM that builds the REAL system prompt from the session each turn
    (so we exercise prompt assembly + schema loading end-to-end, which the
    scripted fake skips) while still returning scripted tool calls."""

    def __init__(self, scripted: list[TurnResult]) -> None:
        self._scripted = list(scripted)
        self.prompts: list[str] = []

    async def run_turn(
        self,
        session: Session,
        *,
        trailing: list[dict[str, object]] | None = None,
    ) -> TurnResult:
        from api.channels.plan import plan_from_schema
        from api.channels.whatsapp.llm import build_system_prompt

        plan = plan_from_schema(session.form_schema, session.language or "en")
        self.prompts.append(build_system_prompt(plan))
        return self._scripted.pop(0) if self._scripted else TurnResult("", [])


class _StaticCrisisLookup:
    def __init__(
        self,
        rows: list[CrisisRow],
        *,
        form: tuple[dict[str, object], int] | None = None,
    ) -> None:
        self._rows = rows
        self._form = form if form is not None else (default_form_schema_copy(), 1)

    async def fetch_by_id(self, crisis_id: uuid.UUID) -> CrisisRow | None:
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
        for r in self._rows:
            if r.id == crisis_id:
                return self._form
        return None


class _FakeSubmitter:
    def __init__(self) -> None:
        self.submitted: list[Session] = []
        self.report_id = uuid.uuid4()

    async def submit(self, session: Session) -> uuid.UUID:
        self.submitted.append(session)
        return self.report_id


def _crisis(name: str) -> CrisisRow:
    return CrisisRow(
        id=uuid.uuid4(),
        name=name,
        status="active",
        created_at=datetime.now(UTC),
    )


def _msg(
    body: str = "",
    *,
    media_url: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
) -> InboundMessage:
    return InboundMessage(
        from_e164="+9665",
        body=body,
        num_media=1 if media_url else 0,
        media_url_0=media_url,
        media_type_0="image/jpeg" if media_url else None,
        latitude=lat,
        longitude=lng,
        button_payload=None,
        list_id=None,
    )


def _tc(tool_name: str, args: dict[str, object] | None = None) -> ToolCall:
    return ToolCall(id=f"tc_{tool_name}", name=tool_name, arguments=dict(args or {}))


def _make_flow(
    crises: list[CrisisRow],
    *,
    llm: _ScriptedLLM | None = None,
    form: tuple[dict[str, object], int] | None = None,
) -> tuple[ConversationFlow, _RecordingProvider, _FakeSubmitter, InMemorySessionStore]:
    provider = _RecordingProvider()
    sessions = InMemorySessionStore()
    crisis_service = CrisisService(
        lookup=_StaticCrisisLookup(crises, form=form), reserved_name="Other / Unspecified"
    )
    submitter = _FakeSubmitter()
    flow = ConversationFlow(
        provider=provider,
        sessions=sessions,
        crises=crisis_service,
        llm=llm or _ScriptedLLM(),
        submitter=submitter,
    )
    return flow, provider, submitter, sessions


CRISIS = _crisis("Aleppo earthquake response")
RESERVED = CrisisRow(
    id=uuid.uuid4(),
    name="Other / Unspecified",
    status="active",
    created_at=datetime.now(UTC),
)


@pytest.mark.asyncio
async def test_first_message_no_deterministic_send() -> None:
    # The LLM owns the first reply. Bot sends nothing on its own — it
    # only sends the LLM's prose.
    llm = _ScriptedLLM([TurnResult(reply_text="مرحباً!", tool_calls=[])])
    flow, provider, _submitter, sessions = _make_flow([CRISIS, RESERVED], llm=llm)
    await flow.handle(_msg("مرحبا"))
    assert provider.lists == []
    assert provider.buttons == []
    assert provider.texts == [("+9665", "مرحباً!")]
    s = await sessions.get("+9665")
    assert s is not None
    assert s.state == "active"
    assert CRISIS.name in s.active_crisis_choices


@pytest.mark.asyncio
async def test_set_crisis_validates_against_snapshot() -> None:
    llm = _ScriptedLLM(
        [
            TurnResult(
                reply_text="Got it.",
                tool_calls=[_tc("set_crisis", {"name": CRISIS.name})],
            )
        ]
    )
    flow, _provider, _submitter, sessions = _make_flow([CRISIS, RESERVED], llm=llm)
    await flow.handle(_msg("Aleppo quake yes"))
    s = await sessions.get("+9665")
    assert s is not None
    assert s.crisis_id == CRISIS.id
    assert s.crisis_name == CRISIS.name


@pytest.mark.asyncio
async def test_set_crisis_invalid_name_is_ignored() -> None:
    llm = _ScriptedLLM(
        [
            TurnResult(
                reply_text="Hmm, didn't recognize that.",
                tool_calls=[_tc("set_crisis", {"name": "Made-up Crisis"})],
            )
        ]
    )
    flow, _provider, _submitter, sessions = _make_flow([CRISIS, RESERVED], llm=llm)
    await flow.handle(_msg("some weather thing"))
    s = await sessions.get("+9665")
    assert s is not None
    assert s.crisis_id is None


@pytest.mark.asyncio
async def test_location_pin_is_captured() -> None:
    llm = _ScriptedLLM()
    flow, _provider, _submitter, sessions = _make_flow([CRISIS, RESERVED], llm=llm)
    await flow.handle(_msg("here", lat=33.5, lng=36.3))
    s = await sessions.get("+9665")
    assert s is not None
    assert s.location == (33.5, 36.3)


@pytest.mark.asyncio
async def test_photo_url_is_captured() -> None:
    llm = _ScriptedLLM()
    flow, _provider, _submitter, sessions = _make_flow([CRISIS, RESERVED], llm=llm)
    await flow.handle(_msg("photo", media_url="https://api.twilio.com/.../Media/ME1"))
    s = await sessions.get("+9665")
    assert s is not None
    assert s.photo_media_url == "https://api.twilio.com/.../Media/ME1"


@pytest.mark.asyncio
async def test_submit_with_incomplete_draft_is_rejected() -> None:
    llm = _ScriptedLLM([TurnResult(reply_text="Submitting!", tool_calls=[_tc("submit_report")])])
    flow, _provider, submitter, sessions = _make_flow([CRISIS, RESERVED], llm=llm)
    await flow.handle(_msg("submit"))
    assert submitter.submitted == []
    # Session is still alive so the LLM can re-prompt on the next turn.
    assert await sessions.get("+9665") is not None


@pytest.mark.asyncio
async def test_full_happy_path() -> None:
    llm = _ScriptedLLM(
        [
            TurnResult(reply_text="Welcome — which crisis?", tool_calls=[]),
            TurnResult(
                reply_text="Got it.",
                tool_calls=[_tc("set_crisis", {"name": CRISIS.name})],
            ),
            TurnResult(reply_text="Thanks for the pin.", tool_calls=[]),
            TurnResult(reply_text="Photo received.", tool_calls=[]),
            TurnResult(
                reply_text="So that's partial damage. Anything else?",
                tool_calls=[_tc("set_damage_class", {"value": "partial"})],
            ),
            TurnResult(
                reply_text="Got the description.",
                tool_calls=[_tc("set_infra_description", {"value": "cracked facade"})],
            ),
            TurnResult(
                reply_text="No debris noted.",
                tool_calls=[_tc("set_debris", {"value": "no"})],
            ),
            TurnResult(
                reply_text="Residential — got it.",
                tool_calls=[_tc("set_infra_type", {"value": ["residential"]})],
            ),
            TurnResult(
                reply_text="Earthquake — got it. Share location?",
                tool_calls=[_tc("set_crisis_nature", {"value": "Earthquake"})],
            ),
            TurnResult(reply_text="Submitting!", tool_calls=[_tc("submit_report")]),
        ]
    )
    flow, provider, submitter, sessions = _make_flow([CRISIS, RESERVED], llm=llm)
    await flow.handle(_msg("hi"))
    await flow.handle(_msg("the Aleppo quake yes"))
    await flow.handle(_msg("here", lat=33.5, lng=36.3))
    await flow.handle(_msg("photo", media_url="https://api.twilio.com/.../Media/ME1"))
    await flow.handle(_msg("partial damage, cracks but standing"))
    await flow.handle(_msg("cracked facade"))
    await flow.handle(_msg("no debris"))
    await flow.handle(_msg("it's a house"))
    await flow.handle(_msg("earthquake"))
    await flow.handle(_msg("yes submit"))
    assert len(submitter.submitted) == 1
    written = submitter.submitted[0]
    assert written.crisis_id == CRISIS.id
    assert written.location == (33.5, 36.3)
    assert written.damage_class == "partial"
    assert written.infra_description == "cracked facade"
    assert written.debris == "no"
    assert written.infra_type == ["residential"]
    assert written.crisis_nature == "Earthquake"
    # Bot sent the post-submit reference code on top of LLM's reply.
    assert any("Reference code" in body for (_to, body) in provider.texts)
    # Session deleted after submission.
    assert await sessions.get("+9665") is None


def _schema_with_generic(*questions: dict[str, object]) -> dict[str, object]:
    schema = default_form_schema_copy()
    schema["pages"].append(
        {
            "kind": "generic",
            "enabled": True,
            "locked": False,
            "title": "Extra questions",
            "questions": list(questions),
        }
    )
    return schema


@pytest.mark.asyncio
async def test_schema_pinned_on_crisis_selection() -> None:
    llm = _ScriptedLLM(
        [TurnResult(reply_text="ok", tool_calls=[_tc("set_crisis", {"name": CRISIS.name})])]
    )
    flow, _provider, _submitter, sessions = _make_flow(
        [CRISIS, RESERVED], llm=llm, form=(default_form_schema_copy(), 7)
    )
    await flow.handle(_msg("the Aleppo quake yes"))
    s = await sessions.get("+9665")
    assert s is not None
    assert s.form_version == 7
    assert s.form_schema is not None


@pytest.mark.asyncio
async def test_prompt_reflects_loaded_schema_after_crisis() -> None:
    # A crisis whose form disables debris and adds a generic question. After
    # the crisis is selected, the next turn's system prompt must list the
    # generic question and must NOT list debris as a field to collect.
    schema = _schema_with_generic(
        {
            "type": "single_select",
            "label": "Roof intact?",
            "required": True,
            "options": [{"label": "Yes"}, {"label": "No"}],
        },
    )
    for page in schema["pages"]:
        if page["kind"] == "debris":
            page["enabled"] = False
    llm = _PromptCapturingLLM(
        [
            TurnResult(reply_text="Which crisis?", tool_calls=[]),
            TurnResult(reply_text="Got it.", tool_calls=[_tc("set_crisis", {"name": CRISIS.name})]),
            TurnResult(reply_text="Send a photo.", tool_calls=[]),
        ]
    )
    provider = _RecordingProvider()
    sessions = InMemorySessionStore()
    crisis_service = CrisisService(
        lookup=_StaticCrisisLookup([CRISIS, RESERVED], form=(schema, 9)),
        reserved_name="Other / Unspecified",
    )
    flow = ConversationFlow(
        provider=provider,
        sessions=sessions,
        crises=crisis_service,
        llm=llm,
        submitter=_FakeSubmitter(),
    )
    await flow.handle(_msg("hi"))
    await flow.handle(_msg("Aleppo quake"))
    await flow.handle(_msg("ok"))

    # The post-crisis prompt (3rd turn) reflects the loaded schema.
    last_prompt = llm.prompts[-1]
    assert "Roof intact?" in last_prompt
    # The numbered built-in debris line only appears when debris is enabled.
    assert "debris (one of:" not in last_prompt


def test_prompt_marks_optional_builtins_skippable() -> None:
    # An enabled-but-not-required built-in select must be advertised to the
    # model as OPTIONAL so it offers the skip; a required one must not be.
    from api.channels.plan import plan_from_schema
    from api.channels.whatsapp.llm import build_system_prompt

    schema = default_form_schema_copy()
    for page in schema["pages"]:
        if page["kind"] == "debris":
            page["enabled"] = True
            page["required"] = False
        if page["kind"] == "infra_type":
            page["enabled"] = True
            page["required"] = True

    prompt = build_system_prompt(plan_from_schema(schema, "en"))
    debris_line = next(ln for ln in prompt.splitlines() if "debris (one of:" in ln)
    infra_line = next(ln for ln in prompt.splitlines() if "infra_type (multi-select" in ln)
    assert "OPTIONAL: when you ask, tell the user they can skip it" in debris_line
    assert "OPTIONAL: when you ask" not in infra_line


def test_prompt_offers_photo_skip_only_when_description_enabled() -> None:
    # With the description page enabled, the photo line must advertise the
    # skip-and-describe alternative; with it disabled, the photo is required.
    from api.channels.plan import plan_from_schema
    from api.channels.whatsapp.llm import build_system_prompt

    with_desc = default_form_schema_copy()
    prompt = build_system_prompt(plan_from_schema(with_desc, "en"))
    photo_line = next(ln for ln in prompt.splitlines() if ln.startswith("2. photo"))
    assert "PHOTO is OPTIONAL" in photo_line
    assert "set_infra_description" in photo_line

    no_desc = default_form_schema_copy()
    for page in no_desc["pages"]:
        if page["kind"] == "description":
            page["enabled"] = False
    prompt = build_system_prompt(plan_from_schema(no_desc, "en"))
    photo_line = next(ln for ln in prompt.splitlines() if ln.startswith("2. photo"))
    assert "photo is REQUIRED" in photo_line


@pytest.mark.asyncio
async def test_set_generic_answer_records_valid_answer() -> None:
    schema = _schema_with_generic(
        {
            "type": "single_select",
            "label": "Roof intact?",
            "required": True,
            "options": [{"label": "Yes"}, {"label": "No"}],
        },
    )
    llm = _ScriptedLLM(
        [
            TurnResult(reply_text="ok", tool_calls=[_tc("set_crisis", {"name": CRISIS.name})]),
            TurnResult(
                reply_text="Noted.",
                tool_calls=[
                    _tc("set_generic_answer", {"question_label": "Roof intact?", "value": "No"})
                ],
            ),
        ]
    )
    flow, _provider, _submitter, sessions = _make_flow(
        [CRISIS, RESERVED], llm=llm, form=(schema, 2)
    )
    await flow.handle(_msg("Aleppo quake"))
    await flow.handle(_msg("roof is gone"))
    s = await sessions.get("+9665")
    assert s is not None
    assert s.generic_answers == {"Roof intact?": "No"}


@pytest.mark.asyncio
async def test_set_generic_answer_invalid_option_is_rejected() -> None:
    schema = _schema_with_generic(
        {
            "type": "single_select",
            "label": "Roof intact?",
            "required": True,
            "options": [{"label": "Yes"}, {"label": "No"}],
        },
    )
    llm = _ScriptedLLM(
        [
            TurnResult(reply_text="ok", tool_calls=[_tc("set_crisis", {"name": CRISIS.name})]),
            TurnResult(
                reply_text="Hmm.",
                tool_calls=[
                    _tc("set_generic_answer", {"question_label": "Roof intact?", "value": "Maybe"})
                ],
            ),
        ]
    )
    flow, _provider, _submitter, sessions = _make_flow(
        [CRISIS, RESERVED], llm=llm, form=(schema, 2)
    )
    await flow.handle(_msg("Aleppo quake"))
    await flow.handle(_msg("not sure"))
    s = await sessions.get("+9665")
    assert s is not None
    assert s.generic_answers == {}  # invalid option not stored


@pytest.mark.asyncio
async def test_multi_select_over_max_is_rejected() -> None:
    schema = _schema_with_generic(
        {
            "type": "multi_select",
            "label": "Needs?",
            "required": True,
            "max_select": 2,
            "options": [{"label": "Food"}, {"label": "Water"}, {"label": "Shelter"}],
        },
    )
    llm = _ScriptedLLM(
        [
            TurnResult(reply_text="ok", tool_calls=[_tc("set_crisis", {"name": CRISIS.name})]),
            TurnResult(
                reply_text="Got it.",
                tool_calls=[
                    _tc(
                        "set_generic_answer",
                        {"question_label": "Needs?", "value": ["Food", "Water", "Shelter"]},
                    )
                ],
            ),
        ]
    )
    flow, _provider, _submitter, sessions = _make_flow(
        [CRISIS, RESERVED], llm=llm, form=(schema, 2)
    )
    await flow.handle(_msg("Aleppo quake"))
    await flow.handle(_msg("everything"))
    s = await sessions.get("+9665")
    assert s is not None
    assert s.generic_answers == {}  # over max_select → rejected


@pytest.mark.asyncio
async def test_required_generic_blocks_submit() -> None:
    schema = _schema_with_generic(
        {"type": "free_text", "label": "Household size?", "required": True},
    )
    llm = _ScriptedLLM(
        [
            TurnResult(reply_text="ok", tool_calls=[_tc("set_crisis", {"name": CRISIS.name})]),
            TurnResult(reply_text="pin/photo", tool_calls=[]),
            TurnResult(
                reply_text="details",
                tool_calls=[
                    _tc("set_damage_class", {"value": "partial"}),
                    _tc("set_debris", {"value": "no"}),
                    _tc("set_infra_type", {"value": ["residential"]}),
                    _tc("set_crisis_nature", {"value": "Earthquake"}),
                ],
            ),
            TurnResult(reply_text="Submitting!", tool_calls=[_tc("submit_report")]),
        ]
    )
    flow, _provider, submitter, sessions = _make_flow([CRISIS, RESERVED], llm=llm, form=(schema, 2))
    await flow.handle(_msg("Aleppo quake"))
    await flow.handle(_msg("here", lat=33.5, lng=36.3))
    await flow.handle(_msg("photo", media_url="https://api.twilio.com/.../Media/ME1"))
    await flow.handle(_msg("submit now"))
    # The required generic question is unanswered → submit rejected.
    assert submitter.submitted == []
    assert await sessions.get("+9665") is not None


@pytest.mark.asyncio
async def test_disabled_builtin_does_not_block_submit() -> None:
    schema = default_form_schema_copy()
    for page in schema["pages"]:
        if page["kind"] == "debris":
            page["enabled"] = False
    llm = _ScriptedLLM(
        [
            TurnResult(reply_text="ok", tool_calls=[_tc("set_crisis", {"name": CRISIS.name})]),
            TurnResult(reply_text="pin/photo", tool_calls=[]),
            TurnResult(
                reply_text="details",
                tool_calls=[
                    _tc("set_damage_class", {"value": "partial"}),
                    _tc("set_infra_type", {"value": ["residential"]}),
                    _tc("set_crisis_nature", {"value": "Earthquake"}),
                ],
            ),
            TurnResult(reply_text="Submitting!", tool_calls=[_tc("submit_report")]),
        ]
    )
    flow, _provider, submitter, _sessions = _make_flow(
        [CRISIS, RESERVED], llm=llm, form=(schema, 2)
    )
    await flow.handle(_msg("Aleppo quake"))
    await flow.handle(_msg("here", lat=33.5, lng=36.3))
    await flow.handle(_msg("photo", media_url="https://api.twilio.com/.../Media/ME1"))
    await flow.handle(_msg("submit now"))
    # debris is disabled for this crisis, so its absence must not block submit.
    assert len(submitter.submitted) == 1


@pytest.mark.asyncio
async def test_cancel_tool_clears_session() -> None:
    llm = _ScriptedLLM(
        [
            TurnResult(reply_text="Hi!", tool_calls=[]),
            TurnResult(reply_text="Okay, cancelled.", tool_calls=[_tc("cancel")]),
        ]
    )
    flow, _provider, _submitter, sessions = _make_flow([CRISIS, RESERVED], llm=llm)
    await flow.handle(_msg("hi"))
    await flow.handle(_msg("never mind"))
    assert await sessions.get("+9665") is None


@pytest.mark.asyncio
async def test_reset_starts_a_new_session() -> None:
    llm = _ScriptedLLM(
        [
            TurnResult(reply_text="Hi!", tool_calls=[]),
            TurnResult(reply_text="ok", tool_calls=[_tc("set_crisis", {"name": CRISIS.name})]),
            TurnResult(reply_text="Restarted.", tool_calls=[]),
        ]
    )
    flow, _provider, _submitter, sessions = _make_flow([CRISIS, RESERVED], llm=llm)
    await flow.handle(_msg("hi"))
    await flow.handle(_msg("yes"))
    s = await sessions.get("+9665")
    assert s is not None and s.crisis_id == CRISIS.id
    await flow.handle(_msg("reset"))
    s = await sessions.get("+9665")
    assert s is not None
    assert s.crisis_id is None
    assert CRISIS.name in s.active_crisis_choices


@pytest.mark.asyncio
async def test_set_debris_rejects_out_of_set() -> None:
    llm = _ScriptedLLM(
        [TurnResult(reply_text="ok", tool_calls=[_tc("set_debris", {"value": "maybe"})])]
    )
    flow, _provider, _submitter, sessions = _make_flow([CRISIS, RESERVED], llm=llm)
    await flow.handle(_msg("hi"))
    s = await sessions.get("+9665")
    assert s is not None
    assert s.debris is None


@pytest.mark.asyncio
async def test_set_infra_type_dedupes_and_filters() -> None:
    llm = _ScriptedLLM(
        [
            TurnResult(
                reply_text="ok",
                tool_calls=[
                    _tc(
                        "set_infra_type",
                        {
                            "value": [
                                "residential",
                                "bogus",  # filtered
                                "residential",  # deduped
                                "community",
                            ],
                            "other_description": "",
                        },
                    )
                ],
            )
        ]
    )
    flow, _provider, _submitter, sessions = _make_flow([CRISIS, RESERVED], llm=llm)
    await flow.handle(_msg("hi"))
    s = await sessions.get("+9665")
    assert s is not None
    assert s.infra_type == ["residential", "community"]
    assert s.infra_type_other is None


@pytest.mark.asyncio
async def test_set_crisis_nature_other_captures_description() -> None:
    llm = _ScriptedLLM(
        [
            TurnResult(
                reply_text="ok",
                tool_calls=[
                    _tc(
                        "set_crisis_nature",
                        {"value": "Other", "other_description": "Locust swarm"},
                    )
                ],
            )
        ]
    )
    flow, _provider, _submitter, sessions = _make_flow([CRISIS, RESERVED], llm=llm)
    await flow.handle(_msg("hi"))
    s = await sessions.get("+9665")
    assert s is not None
    assert s.crisis_nature == "Other"
    assert s.crisis_nature_other == "Locust swarm"


@pytest.mark.asyncio
async def test_submit_blocked_until_all_new_fields_set() -> None:
    # Has crisis + photo + damage_class + location but missing debris,
    # infra_type, crisis_nature => submit must be silently rejected.
    llm = _ScriptedLLM(
        [
            TurnResult(
                reply_text="ok",
                tool_calls=[
                    _tc("set_crisis", {"name": CRISIS.name}),
                    _tc("set_damage_class", {"value": "partial"}),
                    _tc("submit_report"),
                ],
            ),
        ]
    )
    flow, _provider, submitter, sessions = _make_flow([CRISIS, RESERVED], llm=llm)
    await flow.handle(_msg("hi", lat=33.5, lng=36.3, media_url="https://x/Media/1"))
    assert submitter.submitted == []
    assert await sessions.get("+9665") is not None


@pytest.mark.asyncio
async def test_empty_llm_turn_sends_nothing() -> None:
    # LLM produced no tools and no prose — bot stays silent rather than
    # fabricating a fallback.
    llm = _ScriptedLLM([TurnResult(reply_text="", tool_calls=[])])
    flow, provider, _submitter, _sessions = _make_flow([CRISIS, RESERVED], llm=llm)
    await flow.handle(_msg("hi"))
    assert provider.texts == []
    assert provider.lists == []
    assert provider.buttons == []


@pytest.mark.asyncio
async def test_set_route_description_records_directions() -> None:
    llm = _ScriptedLLM(
        [
            TurnResult(
                reply_text="Noted the directions.",
                tool_calls=[
                    _tc("set_route_description", {"value": "Behind the old market, blue gate."})
                ],
            )
        ]
    )
    flow, _provider, _submitter, sessions = _make_flow([CRISIS, RESERVED], llm=llm)
    await flow.handle(_msg("it's behind the old market, blue gate"))
    s = await sessions.get("+9665")
    assert s is not None
    assert s.route_description == "Behind the old market, blue gate."


@pytest.mark.asyncio
async def test_llm_error_sends_generic_message_not_error_text() -> None:
    secret = "AuthenticationError: key sk-live-123 rejected by https://internal.example"
    llm = _ScriptedLLM([TurnResult(reply_text="", tool_calls=[], error=secret)])
    flow, provider, _submitter, _sessions = _make_flow([CRISIS, RESERVED], llm=llm)
    await flow.handle(_msg("hi"))
    assert len(provider.texts) == 1
    body = provider.texts[0][1]
    assert "sk-live-123" not in body
    assert "bot error" not in body
    assert body == strings_for("en").flow_failed
