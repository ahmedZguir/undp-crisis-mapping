"""IVR report walker: the voice sibling of ``sms/flow.py``; each turn returns TwiML.

Select slots are single-pick keypad menus (even ``multi_select``); free-text,
description, location and "Other" answers are recorded and handed to the
``VoiceReportSink`` for transcription. Review offers submit or start over, with
no per-field edit menu.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal, cast

from api.channels.ivr import twiml
from api.channels.ivr.capture import Recording, RecordingField, VoiceReportCapture, VoiceReportSink
from api.channels.ivr.strings import say_lang, strings
from api.channels.languages import SUPPORTED_LANGS, normalize_lang
from api.channels.plan import Slot, plan_from_schema
from api.channels.sessions import Session, SessionStore
from api.channels.values import (
    DAMAGE_VALUES,
    DEBRIS_VALUES,
    INFRA_TYPES,
    NATURE_LABELS,
    NATURE_OTHER,
    nature_label,
)
from api.crises.service import CrisisService

_logger = logging.getLogger(__name__)

Step = Literal[
    "LANGUAGE",
    "CRISIS",
    "DAMAGE",
    "DESCRIPTION",
    "ROUTE",
    "DEBRIS",
    "INFRA_TYPE",
    "INFRA_TYPE_OTHER",
    "CRISIS_NATURE",
    "CRISIS_NATURE_OTHER",
    "GENERIC",
    "REVIEW",
    "DONE",
]

# ``description`` is recorded up front as the photo substitute, so it has no slot step.
_STEP_BY_KIND: dict[str, Step] = {
    "location": "ROUTE",
    "debris": "DEBRIS",
    "infra_type": "INFRA_TYPE",
    "crisis_nature": "CRISIS_NATURE",
}

# Review menu options, in the order the ``review_question`` prompt reads them.
_REVIEW_SUBMIT, _REVIEW_RESTART = 0, 1
_REVIEW_OPTIONS = 2

SKIP_KEY = "0"


def _parse_choice(digits: str | None, n: int) -> int | None:
    if not digits:
        return None
    token = digits.strip().rstrip(twiml.FINISH_KEY)
    if not token.isdigit():
        return None
    k = int(token)
    return k - 1 if 1 <= k <= n else None


def _is_skip(digits: str | None) -> bool:
    return (digits or "").strip().rstrip(twiml.FINISH_KEY) in ("", SKIP_KEY)


def _menu_text(s: dict[str, str], prompt: str, labels: list[str], *, optional: bool) -> str:
    parts = [prompt]
    parts.extend(s["press_for"].format(n=i + 1, label=label) for i, label in enumerate(labels))
    if optional:
        parts.append(s["to_skip"])
    return " ".join(parts)


def _call_sid(session: Session) -> str:
    return session.phone_e164


def _caller_e164(session: Session) -> str:
    return str(session.scratch.get("ivr_from") or "")


class IvrFlow:
    def __init__(
        self,
        *,
        sessions: SessionStore,
        crises: CrisisService,
        sink: VoiceReportSink,
    ) -> None:
        self._sessions = sessions
        self._crises = crises
        self._sink = sink

    async def start(self, *, call_sid: str, from_e164: str) -> str:
        await self._sessions.gc()
        await self._sessions.delete(call_sid)  # defensive: reused CallSid
        session = Session(phone_e164=call_sid)
        session.scratch["ivr_from"] = from_e164
        await self._bootstrap(session)
        out = self._render_step(session, "LANGUAGE", intro=True)
        await self._sessions.upsert(session)
        return out

    async def handle_input(
        self,
        *,
        call_sid: str,
        digits: str | None,
        recording_url: str | None,
        recording_sid: str | None,
        recording_duration: int | None,
    ) -> str:
        session = await self._sessions.get(call_sid)
        if session is None:
            return twiml.say_and_hangup(strings("en")["session_expired"], say_lang(None))

        step = cast(Step, session.scratch.get("ivr_step") or "LANGUAGE")
        _logger.info(
            "ivr.flow.step_in step=%s call=%s digits=%r rec=%s",
            step,
            call_sid,
            digits,
            recording_url,
        )
        out = await self._step(
            session, step, digits, recording_url, recording_sid, recording_duration
        )

        if session.scratch.get("ivr_step") == "DONE":
            await self._sessions.delete(call_sid)
        else:
            await self._sessions.upsert(session)
        return out

    def _lang(self, session: Session) -> str:
        return normalize_lang(session.language)

    async def _bootstrap(self, session: Session) -> None:
        crises = await self._crises.list_active()
        session.scratch["ivr_crisis_by_num"] = {
            str(i + 1): [str(c.id), c.name] for i, c in enumerate(crises)
        }
        session.scratch["ivr_recordings"] = []

    async def _load_form(self, session: Session) -> None:
        if session.crisis_id is None:
            return
        try:
            form = await self._crises.get_form(session.crisis_id)
        except Exception:
            _logger.exception("ivr.flow.load_form_failed")
            return
        if form is not None:
            session.form_schema, session.form_version = form

    def _plan(self, session: Session) -> list[Slot]:
        return plan_from_schema(session.form_schema, self._lang(session))

    def _current_slot(self, session: Session) -> Slot | None:
        plan = self._plan(session)
        idx = session.scratch.get("ivr_slot_idx")
        if isinstance(idx, int) and 0 <= idx < len(plan):
            return plan[idx]
        return None

    def _dispatch_slot(self, session: Session, idx: int) -> str:
        """Description slots are skipped (recorded up front); past the end, REVIEW."""
        plan = self._plan(session)
        while idx < len(plan) and plan[idx].kind == "description":
            idx += 1
        if idx >= len(plan):
            return self._render_step(session, "REVIEW")
        session.scratch["ivr_slot_idx"] = idx
        slot = plan[idx]
        if slot.is_generic:
            return self._render_step(session, "GENERIC")
        return self._render_step(session, _STEP_BY_KIND[slot.kind])

    def _advance(self, session: Session) -> str:
        idx = session.scratch.get("ivr_slot_idx")
        current = idx if isinstance(idx, int) else -1
        return self._dispatch_slot(session, current + 1)

    async def _step(
        self,
        session: Session,
        step: Step,
        digits: str | None,
        recording_url: str | None,
        recording_sid: str | None,
        recording_duration: int | None,
    ) -> str:
        if step == "LANGUAGE":
            return self._on_language(session, digits)
        if step == "CRISIS":
            return await self._on_crisis(session, digits)
        if step == "DAMAGE":
            return self._on_damage(session, digits)
        if step == "DESCRIPTION":
            return self._on_recording(
                session,
                "description",
                "description",
                recording_url,
                recording_sid,
                recording_duration,
                "DESCRIPTION",
                self._after_description,
            )
        if step == "ROUTE":
            return self._on_recording(
                session,
                "route_description",
                "location",
                recording_url,
                recording_sid,
                recording_duration,
                "ROUTE",
                self._advance,
            )
        if step == "DEBRIS":
            return self._on_debris(session, digits)
        if step == "INFRA_TYPE":
            return self._on_infra_type(session, digits)
        if step == "INFRA_TYPE_OTHER":
            return self._on_recording(
                session,
                "infra_type_other",
                "infra_type",
                recording_url,
                recording_sid,
                recording_duration,
                "INFRA_TYPE_OTHER",
                self._advance,
                allow_empty=True,
            )
        if step == "CRISIS_NATURE":
            return self._on_nature(session, digits)
        if step == "CRISIS_NATURE_OTHER":
            return self._on_recording(
                session,
                "crisis_nature_other",
                "crisis_nature",
                recording_url,
                recording_sid,
                recording_duration,
                "CRISIS_NATURE_OTHER",
                self._advance,
                allow_empty=True,
            )
        if step == "GENERIC":
            return self._on_generic(
                session, digits, recording_url, recording_sid, recording_duration
            )
        if step == "REVIEW":
            return await self._on_review(session, digits)
        # Unknown or DONE: restart from crisis.
        return self._render_step(session, "CRISIS")

    def _on_language(self, session: Session, digits: str | None) -> str:
        idx = _parse_choice(digits, len(SUPPORTED_LANGS))
        if idx is None:
            return self._render_step(session, "LANGUAGE")
        session.language = SUPPORTED_LANGS[idx]
        return self._render_step(session, "CRISIS")

    async def _on_crisis(self, session: Session, digits: str | None) -> str:
        by_num = cast(dict[str, list[str]], session.scratch.get("ivr_crisis_by_num") or {})
        idx = _parse_choice(digits, len(by_num))
        if idx is None:
            return self._render_step(session, "CRISIS")
        cid, name = by_num[str(idx + 1)]
        try:
            session.crisis_id = uuid.UUID(cid)
        except ValueError:
            return self._render_step(session, "CRISIS")
        session.crisis_name = name
        await self._load_form(session)
        return self._render_step(session, "DAMAGE")

    def _on_damage(self, session: Session, digits: str | None) -> str:
        idx = _parse_choice(digits, len(DAMAGE_VALUES))
        if idx is None:
            return self._render_step(session, "DAMAGE")
        session.damage_class = DAMAGE_VALUES[idx]
        return self._render_step(session, "DESCRIPTION")

    def _after_description(self, session: Session) -> str:
        return self._dispatch_slot(session, 0)

    def _on_debris(self, session: Session, digits: str | None) -> str:
        if self._slot_optional(session) and _is_skip(digits):
            return self._advance(session)
        idx = _parse_choice(digits, len(DEBRIS_VALUES))
        if idx is None:
            return self._render_step(session, "DEBRIS")
        session.debris = DEBRIS_VALUES[idx]
        return self._advance(session)

    def _on_infra_type(self, session: Session, digits: str | None) -> str:
        if self._slot_optional(session) and _is_skip(digits):
            return self._advance(session)
        idx = _parse_choice(digits, len(INFRA_TYPES))
        if idx is None:
            return self._render_step(session, "INFRA_TYPE")
        value = INFRA_TYPES[idx]
        session.infra_type = [value]
        if value == "other":
            return self._render_step(session, "INFRA_TYPE_OTHER")
        return self._advance(session)

    def _on_nature(self, session: Session, digits: str | None) -> str:
        if self._slot_optional(session) and _is_skip(digits):
            return self._advance(session)
        n = len(NATURE_LABELS)
        idx = _parse_choice(digits, n + 1)  # +1 for the trailing "Other"
        if idx is None:
            return self._render_step(session, "CRISIS_NATURE")
        if idx == n:  # the "Other" pick
            session.crisis_nature = NATURE_OTHER
            return self._render_step(session, "CRISIS_NATURE_OTHER")
        session.crisis_nature = NATURE_LABELS[idx]
        return self._advance(session)

    def _on_generic(
        self,
        session: Session,
        digits: str | None,
        recording_url: str | None,
        recording_sid: str | None,
        recording_duration: int | None,
    ) -> str:
        slot = self._current_slot(session)
        if slot is None or not slot.is_generic:
            return self._advance(session)
        if slot.qtype == "free_text":
            return self._on_recording(
                session,
                "generic_answer",
                slot.target,
                recording_url,
                recording_sid,
                recording_duration,
                "GENERIC",
                self._advance,
            )
        # single_select and multi_select are both single-pick over voice.
        if not slot.required and _is_skip(digits):
            return self._advance(session)
        idx = _parse_choice(digits, len(slot.options))
        if idx is None:
            return self._render_step(session, "GENERIC")
        session.generic_answers[slot.target] = slot.options[idx]
        return self._advance(session)

    def _on_recording(
        self,
        session: Session,
        field: RecordingField,
        slot_target: str,
        recording_url: str | None,
        recording_sid: str | None,
        recording_duration: int | None,
        retry_step: Step,
        on_done: Callable[[Session], str],
        *,
        allow_empty: bool = False,
    ) -> str:
        """Stash the recording and advance; with none, re-prompt unless allow_empty."""
        if not recording_url:
            if allow_empty:
                return on_done(session)
            return self._render_step(session, retry_step)
        recs = cast(list[dict[str, Any]], session.scratch.setdefault("ivr_recordings", []))
        recs.append(
            {
                "field": field,
                "slot_target": slot_target,
                "url": recording_url,
                "sid": recording_sid,
                "duration": recording_duration,
            }
        )
        return on_done(session)

    async def _on_review(self, session: Session, digits: str | None) -> str:
        idx = _parse_choice(digits, _REVIEW_OPTIONS)
        if idx == _REVIEW_SUBMIT:
            return await self._do_submit(session)
        if idx == _REVIEW_RESTART:
            return self._restart(session)
        return self._render_step(session, "REVIEW")

    def _restart(self, session: Session) -> str:
        session.crisis_id = None
        session.crisis_name = None
        session.damage_class = None
        session.debris = None
        session.infra_type = None
        session.crisis_nature = None
        session.generic_answers = {}
        session.form_schema = None
        session.form_version = None
        session.scratch["ivr_recordings"] = []
        session.scratch.pop("ivr_slot_idx", None)
        return self._render_step(session, "LANGUAGE", intro=True)

    def _render_step(self, session: Session, step: Step, *, intro: bool = False) -> str:
        session.scratch["ivr_step"] = step
        s = strings(self._lang(session))
        lang = say_lang(session.language)

        if step == "LANGUAGE":
            # Each option is spoken in its own language's voice.
            segments = [
                (strings(code)["lang_self_prompt"].format(n=i + 1), say_lang(code))
                for i, code in enumerate(SUPPORTED_LANGS)
            ]
            intro_segs = [(s["intro"], lang)] if intro else None
            return twiml.gather_segments(segments, len(SUPPORTED_LANGS), intro=intro_segs)

        if step == "CRISIS":
            by_num = cast(dict[str, list[str]], session.scratch.get("ivr_crisis_by_num") or {})
            if not by_num:
                session.scratch["ivr_step"] = "DONE"
                return twiml.say_and_hangup(s["no_active_crises"], lang)
            labels = [pair[1] for _num, pair in sorted(by_num.items(), key=lambda kv: int(kv[0]))]
            return twiml.gather(
                _menu_text(s, s["ask_crisis"], labels, optional=False), lang, len(labels)
            )

        if step == "DAMAGE":
            labels = [s["damage_minimal"], s["damage_partial"], s["damage_complete"]]
            return twiml.gather(
                _menu_text(s, s["ask_damage"], labels, optional=False), lang, len(DAMAGE_VALUES)
            )

        if step == "DESCRIPTION":
            return twiml.record(s["ask_description"], lang)

        if step == "ROUTE":
            return twiml.record(s["ask_route"], lang)

        if step == "DEBRIS":
            optional = self._slot_optional(session)
            labels = [s["debris_yes"], s["debris_no"], s["debris_unknown"]]
            return twiml.gather(
                _menu_text(s, s["ask_debris"], labels, optional=optional),
                lang,
                len(DEBRIS_VALUES),
            )

        if step == "INFRA_TYPE":
            optional = self._slot_optional(session)
            labels = [s[f"infra_{v}"] for v in INFRA_TYPES]
            return twiml.gather(
                _menu_text(s, s["ask_infra"], labels, optional=optional), lang, len(labels)
            )

        if step == "INFRA_TYPE_OTHER":
            return twiml.record(s["ask_infra_other"], lang)

        if step == "CRISIS_NATURE":
            optional = self._slot_optional(session)
            nature_lang = self._lang(session)
            labels = [*(nature_label(v, nature_lang) for v in NATURE_LABELS), s["nature_other"]]
            return twiml.gather(
                _menu_text(s, s["ask_nature"], labels, optional=optional), lang, len(labels)
            )

        if step == "CRISIS_NATURE_OTHER":
            return twiml.record(s["ask_nature_other"], lang)

        if step == "GENERIC":
            slot = self._current_slot(session)
            if slot is None or not slot.is_generic:
                return self._advance(session)
            if slot.qtype == "free_text":
                return twiml.record(f"{slot.target}. {s['generic_record_hint']}", lang)
            labels = list(slot.options)
            text = _menu_text(s, f"{slot.target}.", labels, optional=not slot.required)
            return twiml.gather(text, lang, len(labels))

        if step == "REVIEW":
            return twiml.gather(
                s["review_question"],
                lang,
                _REVIEW_OPTIONS,
                intro=self._render_review(session, s),
            )

        # Unreachable today: nothing renders "DONE".
        return twiml.say_and_hangup(s["goodbye"], lang)

    def _slot_optional(self, session: Session) -> bool:
        slot = self._current_slot(session)
        return slot is not None and not slot.required

    def _render_review(self, session: Session, s: dict[str, str]) -> str:
        lines = [s["review_header"]]
        lines.append(f"{s['label_crisis']}: {session.crisis_name or '-'}.")
        if session.damage_class is not None:
            lines.append(f"{s['label_damage']}: {s[f'damage_{session.damage_class}']}.")
        if session.debris is not None:
            lines.append(f"{s['label_debris']}: {s[f'debris_{session.debris}']}.")
        if session.infra_type:
            infra = session.infra_type[0]
            lines.append(f"{s['label_infra']}: {s.get(f'infra_{infra}', infra)}.")
        if session.crisis_nature is not None:
            nature = (
                s["nature_other"]
                if session.crisis_nature == NATURE_OTHER
                else nature_label(session.crisis_nature, self._lang(session))
            )
            lines.append(f"{s['label_nature']}: {nature}.")
        lines.append(s["review_recorded"])
        return " ".join(lines)

    def _build_capture(self, session: Session) -> VoiceReportCapture:
        recs_raw = cast(list[dict[str, Any]], session.scratch.get("ivr_recordings") or [])
        recordings = [
            Recording(
                field=cast(RecordingField, str(r["field"])),
                slot_target=str(r["slot_target"]),
                url=str(r["url"]),
                sid=(str(r["sid"]) if r.get("sid") else None),
                duration_seconds=(int(r["duration"]) if r.get("duration") is not None else None),
            )
            for r in recs_raw
        ]
        # Free-text generics arrive as recordings, not session answers.
        generic = {k: v for k, v in session.generic_answers.items() if isinstance(v, str)}
        assert session.crisis_id is not None  # REVIEW is unreachable without a crisis
        return VoiceReportCapture(
            call_sid=_call_sid(session),
            from_e164=_caller_e164(session),
            crisis_id=session.crisis_id,
            captured_at=datetime.now(UTC),
            client_submission_id=session.client_submission_id,
            language=session.language,
            damage_class=session.damage_class,
            debris=session.debris,
            infra_type=list(session.infra_type) if session.infra_type else None,
            crisis_nature=session.crisis_nature,
            crisis_nature_is_other=session.crisis_nature == NATURE_OTHER,
            generic_answers=generic,
            form_schema=session.form_schema,
            form_version=session.form_version,
            recordings=recordings,
        )

    async def _do_submit(self, session: Session) -> str:
        s = strings(self._lang(session))
        lang = say_lang(session.language)
        session.scratch["ivr_step"] = "DONE"
        try:
            report_id = await self._sink.capture(self._build_capture(session))
        except Exception:
            _logger.exception("ivr.flow.submit_failed")
            return twiml.say_and_hangup(s["submit_failed"], lang)
        if report_id is None:
            # Processed after the call, so there is no reference yet.
            return twiml.say_and_hangup(s["received"], lang)
        return twiml.say_and_hangup(s["submitted"].format(ref=str(report_id)[:8]), lang)
