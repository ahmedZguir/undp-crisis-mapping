"""SMS report walker: the numbered-text sibling of ``whatsapp/template_flow.py``.

SMS carries neither media nor a pin, so under the minimum-content rule
a mandatory description
replaces the photo and the ``location`` slot is collected as route directions.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from typing import Literal, Protocol, cast

from api.channels.languages import LANGUAGE_NATIVE_NAMES, SUPPORTED_LANGS, normalize_lang
from api.channels.plan import Slot, missing_optional_builtins_and_generics, plan_from_schema
from api.channels.sessions import LanguagePrefs, Session, SessionStore
from api.channels.sms.strings import strings
from api.channels.submitter import ReportSubmitter
from api.channels.values import (
    DAMAGE_VALUES,
    DEBRIS_VALUES,
    INFRA_TYPES,
    MAX_DESCRIPTION_CHARS,
    MAX_OTHER_TEXT_CHARS,
    MAX_ROUTE_CHARS,
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
    "EDIT_MENU",
    "DONE",
]

_RESTART_WORDS = frozenset({"restart", "reset", "start over", "start", "إعادة", "اعادة"})
_STOP_WORDS = frozenset({"stop", "cancel", "إلغاء", "الغاء"})

# ``description`` is collected up front as the photo substitute, so it has no slot step.
_STEP_BY_KIND: dict[str, Step] = {
    "location": "ROUTE",
    "debris": "DEBRIS",
    "infra_type": "INFRA_TYPE",
    "crisis_nature": "CRISIS_NATURE",
}

# Review menu options, in the order the ``review_question`` copy lists them.
_REVIEW_SUBMIT, _REVIEW_EDIT, _REVIEW_CANCEL = range(3)
_REVIEW_OPTIONS = 3

_LOG_PREVIEW_CHARS = 160
_EDIT_LABEL_CHARS = 40


def _parse_index(body: str, n: int) -> int | None:
    token = body.strip()
    if token.isdigit():
        k = int(token)
        if 1 <= k <= n:
            return k - 1
    return None


def _parse_index_list(body: str, n: int) -> list[int] | None:
    """Comma/space-separated 1-based picks to de-duplicated 0-based indices.

    Empty or "0" gives []; any invalid token gives None.
    """
    raw = body.strip()
    if not raw or raw == "0":
        return []
    out: list[int] = []
    for part in raw.replace(",", " ").split():
        if not part.isdigit():
            return None
        k = int(part)
        if not (1 <= k <= n):
            return None
        if k - 1 not in out:
            out.append(k - 1)
    return out


def _is_skip(body: str) -> bool:
    return body.strip() == "0"


def _optional_other_text(body: str) -> str | None:
    text = body.strip()
    if text and not _is_skip(body):
        return text[:MAX_OTHER_TEXT_CHARS]
    return None


def _numbered(options: Sequence[str]) -> str:
    return "\n".join(f"{i + 1}. {opt}" for i, opt in enumerate(options))


# Left-to-right mark for the multilingual language menu only: without it the
# bidi algorithm right-aligns the Arabic row ("2. العربية") away from the others.
_LRM = "\u200e"


def _language_menu(s: dict[str, str]) -> str:
    rows = "\n".join(
        f"{_LRM}{i + 1}. {LANGUAGE_NATIVE_NAMES[code]}" for i, code in enumerate(SUPPORTED_LANGS)
    )
    return f"{s['ask_language']}\n{rows}\n\n{s['lang_reply_hint']}"


class TextSender(Protocol):
    async def send_text(self, to: str, body: str) -> None: ...


class SmsTemplateFlow:
    def __init__(
        self,
        *,
        provider: TextSender,
        sessions: SessionStore,
        crises: CrisisService,
        submitter: ReportSubmitter,
        lang_prefs: LanguagePrefs,
        privacy_policy_url: str = "",
    ) -> None:
        self._provider = provider
        self._sessions = sessions
        self._crises = crises
        self._submitter = submitter
        self._lang_prefs = lang_prefs
        self._privacy_policy_url = privacy_policy_url

    async def _send(self, to: str, body: str) -> None:
        _logger.info("sms.flow.reply to=%s body=%r", to, body[:_LOG_PREVIEW_CHARS])
        await self._provider.send_text(to, body)

    async def handle(self, from_e164: str, body: str) -> None:
        _logger.info("sms.flow.handle from=%s body=%r", from_e164, body)
        await self._sessions.gc()
        session = await self._sessions.get(from_e164)

        word = body.strip().lower()
        if session is not None and word in _STOP_WORDS:
            s = self._s(session)
            await self._send(from_e164, s["cancelled"])
            await self._sessions.delete(from_e164)
            return

        restart = word in _RESTART_WORDS
        if session is None or restart:
            if session is not None:
                await self._sessions.delete(session.phone_e164)
            session = Session(phone_e164=from_e164)
            await self._bootstrap(session)
            remembered = self._lang_prefs.get(from_e164)
            if remembered is not None:
                session.language = remembered
                await self._send_step(session, "CRISIS", intro=True)
            else:
                await self._send_step(session, "LANGUAGE")
            await self._sessions.upsert(session)
            return

        step = cast(Step, session.scratch.get("sms_step") or "LANGUAGE")
        _logger.info("sms.flow.step_in step=%s from=%s", step, from_e164)
        await self._step(session, step, body)

        if session.scratch.get("sms_step") == "DONE":
            await self._sessions.delete(session.phone_e164)
        else:
            await self._sessions.upsert(session)

    def _lang(self, session: Session) -> str:
        return normalize_lang(session.language)

    def _s(self, session: Session) -> dict[str, str]:
        return strings(self._lang(session))

    async def _bootstrap(self, session: Session) -> None:
        crises = await self._crises.list_active()
        # "1" -> [id, name]
        session.scratch["sms_crisis_by_num"] = {
            str(i + 1): [str(c.id), c.name] for i, c in enumerate(crises)
        }

    async def _load_form(self, session: Session) -> None:
        if session.crisis_id is None:
            return
        try:
            form = await self._crises.get_form(session.crisis_id)
        except Exception:
            _logger.exception("sms.flow.load_form_failed")
            return
        if form is not None:
            session.form_schema, session.form_version = form

    def _plan(self, session: Session) -> list[Slot]:
        return plan_from_schema(session.form_schema, self._lang(session))

    def _current_slot(self, session: Session) -> Slot | None:
        plan = self._plan(session)
        idx = session.scratch.get("sms_slot_idx")
        if isinstance(idx, int) and 0 <= idx < len(plan):
            return plan[idx]
        return None

    def _current_is_optional(self, session: Session) -> bool:
        slot = self._current_slot(session)
        return slot is not None and not slot.is_generic and not slot.required

    async def _dispatch_slot(self, session: Session, idx: int) -> None:
        """Description slots are skipped (asked up front); past the end, REVIEW."""
        plan = self._plan(session)
        while idx < len(plan) and plan[idx].kind == "description":
            idx += 1
        if idx >= len(plan):
            await self._send_step(session, "REVIEW")
            return
        session.scratch["sms_slot_idx"] = idx
        slot = plan[idx]
        if slot.is_generic:
            await self._send_step(session, "GENERIC")
            return
        await self._send_step(session, _STEP_BY_KIND[slot.kind])

    async def _back_to_review_if_editing(self, session: Session) -> bool:
        if session.scratch.pop("sms_editing", None):
            await self._send_step(session, "REVIEW")
            return True
        return False

    async def _skipped_optional(self, session: Session, body: str) -> bool:
        if self._current_is_optional(session) and _is_skip(body):
            await self._advance(session)
            return True
        return False

    async def _advance(self, session: Session) -> None:
        if await self._back_to_review_if_editing(session):
            return
        idx = session.scratch.get("sms_slot_idx")
        current = idx if isinstance(idx, int) else -1
        await self._dispatch_slot(session, current + 1)

    async def _after_damage(self, session: Session) -> None:
        if await self._back_to_review_if_editing(session):
            return
        await self._send_step(session, "DESCRIPTION")

    async def _after_description(self, session: Session) -> None:
        if await self._back_to_review_if_editing(session):
            return
        await self._dispatch_slot(session, 0)

    async def _step(self, session: Session, step: Step, body: str) -> None:
        handlers = {
            "LANGUAGE": self._on_language,
            "CRISIS": self._on_crisis,
            "DAMAGE": self._on_damage,
            "DESCRIPTION": self._on_description,
            "ROUTE": self._on_route,
            "DEBRIS": self._on_debris,
            "INFRA_TYPE": self._on_infra_type,
            "INFRA_TYPE_OTHER": self._on_infra_type_other,
            "CRISIS_NATURE": self._on_nature,
            "CRISIS_NATURE_OTHER": self._on_nature_other,
            "GENERIC": self._on_generic,
            "REVIEW": self._on_review,
            "EDIT_MENU": self._on_edit_menu,
        }
        fn = handlers.get(step)
        if fn is None:
            await self._send_step(session, "CRISIS")
            return
        await fn(session, body)

    async def _on_language(self, session: Session, body: str) -> None:
        idx = _parse_index(body, len(SUPPORTED_LANGS))
        if idx is None:
            await self._send_step(session, "LANGUAGE")
            return
        session.language = SUPPORTED_LANGS[idx]
        self._lang_prefs.set(session.phone_e164, session.language)
        await self._send_step(session, "CRISIS", intro=True)

    async def _on_crisis(self, session: Session, body: str) -> None:
        by_num = cast(dict[str, list[str]], session.scratch.get("sms_crisis_by_num") or {})
        idx = _parse_index(body, len(by_num))
        if idx is None:
            await self._send_step(session, "CRISIS")
            return
        cid, name = by_num[str(idx + 1)]
        try:
            session.crisis_id = uuid.UUID(cid)
        except ValueError:
            await self._send_step(session, "CRISIS")
            return
        session.crisis_name = name
        await self._load_form(session)
        await self._send_step(session, "DAMAGE")

    async def _on_damage(self, session: Session, body: str) -> None:
        idx = _parse_index(body, len(DAMAGE_VALUES))
        if idx is None:
            await self._send_step(session, "DAMAGE")
            return
        session.damage_class = DAMAGE_VALUES[idx]
        await self._after_damage(session)

    async def _on_description(self, session: Session, body: str) -> None:
        text = body.strip()
        if not text:
            await self._send_step(session, "DESCRIPTION")
            return
        session.infra_description = text[:1024]
        await self._after_description(session)

    async def _on_route(self, session: Session, body: str) -> None:
        text = body.strip()
        if not text:
            await self._send_step(session, "ROUTE")
            return
        session.route_description = text[:MAX_ROUTE_CHARS]
        await self._advance(session)

    async def _on_debris(self, session: Session, body: str) -> None:
        if await self._skipped_optional(session, body):
            return
        idx = _parse_index(body, len(DEBRIS_VALUES))
        if idx is None:
            await self._send_step(session, "DEBRIS")
            return
        session.debris = DEBRIS_VALUES[idx]
        await self._advance(session)

    async def _on_infra_type(self, session: Session, body: str) -> None:
        if await self._skipped_optional(session, body):
            return
        idx = _parse_index(body, len(INFRA_TYPES))
        if idx is None:
            await self._send_step(session, "INFRA_TYPE")
            return
        value = INFRA_TYPES[idx]
        session.infra_type = [value]
        if value == "other":
            await self._send_step(session, "INFRA_TYPE_OTHER")
            return
        await self._advance(session)

    async def _on_infra_type_other(self, session: Session, body: str) -> None:
        other = _optional_other_text(body)
        if other is not None:
            session.infra_type_other = other
        await self._advance(session)

    async def _on_nature(self, session: Session, body: str) -> None:
        if await self._skipped_optional(session, body):
            return
        n = len(NATURE_LABELS)
        idx = _parse_index(body, n + 1)  # +1 for the trailing "Other" row
        if idx is None:
            await self._send_step(session, "CRISIS_NATURE")
            return
        if idx == n:
            session.crisis_nature = NATURE_OTHER
            await self._send_step(session, "CRISIS_NATURE_OTHER")
            return
        session.crisis_nature = NATURE_LABELS[idx]
        await self._advance(session)

    async def _on_nature_other(self, session: Session, body: str) -> None:
        other = _optional_other_text(body)
        if other is not None:
            session.crisis_nature_other = other
        await self._advance(session)

    async def _on_generic(self, session: Session, body: str) -> None:
        slot = self._current_slot(session)
        if slot is None or not slot.is_generic:
            await self._advance(session)
            return
        s = self._s(session)
        label = slot.target

        if not slot.required and _is_skip(body):
            await self._advance(session)
            return

        if slot.qtype == "free_text":
            text = body.strip()
            if not text:
                await self._send_step(session, "GENERIC")
                return
            session.generic_answers[label] = text[:MAX_DESCRIPTION_CHARS]
            await self._advance(session)
            return

        if slot.qtype == "single_select":
            idx = _parse_index(body, len(slot.options))
            if idx is None:
                await self._send(session.phone_e164, s["invalid_choice"])
                await self._send_step(session, "GENERIC")
                return
            session.generic_answers[label] = slot.options[idx]
            await self._advance(session)
            return

        picks = _parse_index_list(body, len(slot.options))
        if picks is None:
            await self._send(session.phone_e164, s["invalid_choice"])
            await self._send_step(session, "GENERIC")
            return
        if slot.required and not picks:
            await self._send(session.phone_e164, s["at_least_one"])
            await self._send_step(session, "GENERIC")
            return
        if slot.max_select is not None and len(picks) > slot.max_select:
            await self._send(session.phone_e164, s["generic_max"].format(n=slot.max_select))
            await self._send_step(session, "GENERIC")
            return
        session.generic_answers[label] = [slot.options[i] for i in picks]
        await self._advance(session)

    async def _on_review(self, session: Session, body: str) -> None:
        s = self._s(session)
        idx = _parse_index(body, _REVIEW_OPTIONS)
        if idx == _REVIEW_SUBMIT:
            if self._missing(session):
                await self._send_step(session, "REVIEW")
                return
            if await self._do_submit(session):
                session.scratch["sms_step"] = "DONE"
            return
        if idx == _REVIEW_EDIT:
            await self._send_step(session, "EDIT_MENU")
            return
        if idx == _REVIEW_CANCEL:
            await self._send(session.phone_e164, s["cancelled"])
            session.scratch["sms_step"] = "DONE"
            return
        await self._send_step(session, "REVIEW")

    def _edit_targets(self, session: Session) -> list[tuple[Step, str, int | None]]:
        s = self._s(session)
        targets: list[tuple[Step, str, int | None]] = [
            ("DAMAGE", s["label_damage"], None),
            ("DESCRIPTION", s["label_description"], None),
        ]
        label_by_kind = {
            "location": s["label_route"],
            "debris": s["label_debris"],
            "infra_type": s["label_infra"],
            "crisis_nature": s["label_nature"],
        }
        for i, slot in enumerate(self._plan(session)):
            if slot.kind == "description":
                continue
            if slot.is_generic:
                targets.append(("GENERIC", slot.target[:_EDIT_LABEL_CHARS], i))
            else:
                targets.append((_STEP_BY_KIND[slot.kind], label_by_kind[slot.kind], None))
        return targets

    async def _on_edit_menu(self, session: Session, body: str) -> None:
        targets = self._edit_targets(session)
        idx = _parse_index(body, len(targets))
        if idx is None:
            await self._send_step(session, "EDIT_MENU")
            return
        step, _label, slot_idx = targets[idx]
        session.scratch["sms_editing"] = True
        if step == "GENERIC" and slot_idx is not None:
            session.scratch["sms_slot_idx"] = slot_idx
        await self._send_step(session, step)

    def _missing(self, session: Session) -> list[str]:
        """Unset required fields; description and route_description satisfy the content gate."""
        plan = self._plan(session)
        missing: list[str] = []
        if session.crisis_id is None:
            missing.append("crisis")
        if session.damage_class is None:
            missing.append("damage_class")
        if not (session.infra_description and session.infra_description.strip()):
            missing.append("description")
        if not (session.route_description and session.route_description.strip()):
            missing.append("route_description")
        missing.extend(missing_optional_builtins_and_generics(session, plan))
        return missing

    async def _send_step(self, session: Session, step: Step, *, intro: bool = False) -> None:
        session.scratch["sms_step"] = step
        s = self._s(session)
        to = session.phone_e164

        if step == "LANGUAGE":
            await self._send(to, _language_menu(s))
            return
        if step == "CRISIS":
            by_num = cast(dict[str, list[str]], session.scratch.get("sms_crisis_by_num") or {})
            if not by_num:
                await self._send(to, s["no_active_crises"])
                return
            menu = "\n".join(f"{num}. {pair[1]}" for num, pair in sorted(by_num.items()))
            if intro:
                consent = s["consent_notice"].format(privacy_url=self._privacy_policy_url)
                prefix = s["intro"] + "\n\n" + consent + "\n\n"
            else:
                prefix = ""
            await self._send(to, f"{prefix}{s['ask_crisis']}\n\n{menu}")
            return
        if step == "DAMAGE":
            await self._send(to, s["ask_damage"])
            return
        if step == "DESCRIPTION":
            await self._send(to, s["ask_description"])
            return
        if step == "ROUTE":
            await self._send(to, s["ask_route"])
            return
        skip_hint = f"\n\n{s['generic_skip_hint']}" if self._current_is_optional(session) else ""
        if step == "DEBRIS":
            await self._send(to, f"{s['ask_debris']}{skip_hint}")
            return
        if step == "INFRA_TYPE":
            labels = tuple(s[f"infra_{v}"] for v in INFRA_TYPES)
            await self._send(to, f"{s['ask_infra']}\n\n{_numbered(labels)}{skip_hint}")
            return
        if step == "INFRA_TYPE_OTHER":
            await self._send(to, f"{s['prompt_infra_other']} {s['generic_skip_hint']}")
            return
        if step == "CRISIS_NATURE":
            lang = self._lang(session)
            options = (*(nature_label(v, lang) for v in NATURE_LABELS), s["nature_other"])
            await self._send(to, f"{s['ask_nature']}\n\n{_numbered(options)}{skip_hint}")
            return
        if step == "CRISIS_NATURE_OTHER":
            await self._send(to, f"{s['prompt_nature_other']} {s['generic_skip_hint']}")
            return
        if step == "GENERIC":
            slot = self._current_slot(session)
            if slot is None or not slot.is_generic:
                await self._advance(session)
                return
            await self._send_generic(session, slot, s)
            return
        if step == "REVIEW":
            await self._send(to, self._render_review(session, s))
            await self._send(to, s["review_question"])
            return
        if step == "EDIT_MENU":
            menu = _numbered([label for _step, label, _idx in self._edit_targets(session)])
            await self._send(to, f"{s['ask_edit']}\n\n{menu}")
            return

    async def _send_generic(self, session: Session, slot: Slot, s: dict[str, str]) -> None:
        to = session.phone_e164
        label = slot.target
        if slot.qtype == "free_text":
            hint = s["generic_free_hint"]
            if not slot.required:
                hint += " " + s["generic_skip_hint"]
            await self._send(to, f"{label}\n\n{hint}")
            return
        numbered = _numbered(slot.options)
        if slot.qtype == "multi_select":
            instruction = s["generic_pick_numbers"]
            if slot.max_select is not None:
                instruction += " " + s["generic_max"].format(n=slot.max_select)
        else:
            instruction = s["generic_pick_number"]
        if not slot.required:
            instruction += " " + s["generic_skip_hint"]
        await self._send(to, f"{label}\n\n{numbered}\n\n{instruction}")

    def _render_review(self, session: Session, s: dict[str, str]) -> str:
        lines = [s["review_header"], ""]
        lines.append(f"{s['label_crisis']}: {session.crisis_name or '-'}")
        damage = session.damage_class
        if damage is not None:
            lines.append(f"{s['label_damage']}: {s[f'damage_{damage}']}")
        if session.infra_description:
            lines.append(f"{s['label_description']}: {session.infra_description}")
        if session.route_description:
            lines.append(f"{s['label_route']}: {session.route_description}")
        if session.debris is not None:
            lines.append(f"{s['label_debris']}: {s[f'debris_{session.debris}']}")
        if session.infra_type:
            infra = ", ".join(s.get(f"infra_{v}", v) for v in session.infra_type)
            if session.infra_type_other:
                infra += f" ({session.infra_type_other})"
            lines.append(f"{s['label_infra']}: {infra}")
        if session.crisis_nature is not None:
            lang = self._lang(session)
            if session.crisis_nature == NATURE_OTHER:
                nature = s["nature_other"]
                if session.crisis_nature_other:
                    nature = f"{nature}: {session.crisis_nature_other}"
            else:
                nature = nature_label(session.crisis_nature, lang)
            lines.append(f"{s['label_nature']}: {nature}")
        for label, value in session.generic_answers.items():
            shown = ", ".join(value) if isinstance(value, list) else value
            lines.append(f"{label}: {shown}")
        return "\n".join(lines)

    async def _do_submit(self, session: Session) -> bool:
        s = self._s(session)
        try:
            report_id = await self._submitter.submit(session)
        except Exception:
            _logger.exception("sms.flow.submit_failed")
            await self._send(session.phone_e164, s["submit_failed"])
            return False
        await self._send(session.phone_e164, s["submitted"].format(ref=str(report_id)[:8]))
        session.state = "done"
        return True
