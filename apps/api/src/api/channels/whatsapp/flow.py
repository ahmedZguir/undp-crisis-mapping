"""LLM-driven WhatsApp conversation: the bot does only the plumbing."""

from __future__ import annotations

import base64
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Literal, cast

from api.channels.languages import SUPPORTED_LANGS, normalize_lang
from api.channels.plan import Slot, missing_fields, plan_for_session
from api.channels.sessions import HistoryMsg, Session, SessionStore
from api.channels.submitter import ReportSubmitter
from api.channels.values import (
    DAMAGE_VALUES,
    DEBRIS_VALUES,
    INFRA_TYPES,
    MAX_ROUTE_CHARS,
    NATURE_LABELS,
    NATURE_OTHER,
    is_damage_class,
    is_debris,
)
from api.channels.whatsapp.adapter import Provider
from api.channels.whatsapp.llm import LLMClient, ToolCall
from api.channels.whatsapp.messages import strings_for
from api.core.config import get_settings
from api.crises.service import CrisisService

_logger = logging.getLogger(__name__)

_MAX_CRISIS_CHOICES = 10
# Chained tool-call hops per turn (e.g. set_language then set_crisis).
_MAX_TOOL_HOPS = 3
_INFRA_TYPE_VALUES: frozenset[str] = frozenset(INFRA_TYPES)
_CRISIS_NATURE_VALUES: frozenset[str] = frozenset((*NATURE_LABELS, NATURE_OTHER))


@dataclass(frozen=True)
class InboundMessage:
    """Twilio sets ``media_url_0`` (fetched at submit); Meta ``media_bytes`` (route-fetched)."""

    from_e164: str
    body: str
    num_media: int
    media_url_0: str | None
    media_type_0: str | None
    latitude: float | None
    longitude: float | None
    button_payload: str | None
    list_id: str | None
    media_bytes: bytes | None = None
    media_mime: str | None = None
    # Parsed nfm_reply.response_json, set only on a WhatsApp Flow completion.
    flow_response: dict[str, object] | None = None


def capture_photo(session: Session, msg: InboundMessage) -> Literal["stored", "invalid", "none"]:

    mime = msg.media_type_0 or msg.media_mime
    if mime and not mime.startswith("image/"):
        return "invalid"
    if msg.media_bytes is not None:
        session.photo_bytes = msg.media_bytes
        session.photo_mime = msg.media_mime
        return "stored"
    if msg.media_url_0 is not None:
        session.photo_media_url = msg.media_url_0
        return "stored"
    return "none"


def parse_reset(body: str) -> bool:
    return body.strip().lower() in {"reset", "restart", "/start", "إعادة"}


def _assistant_tool_call_msgs(
    calls: list[ToolCall],
    results: dict[str, str],
) -> list[dict[str, object]]:
    """The assistant tool-call message plus one tool-result message per call.

    Results carry the real status: reporting a failed ``submit_report`` as ok
    would make the model tell the user it succeeded.
    """
    out: list[dict[str, object]] = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {
                        "name": c.name,
                        "arguments": json.dumps(c.arguments, ensure_ascii=False),
                    },
                }
                for c in calls
            ],
        }
    ]
    for c in calls:
        out.append(
            {
                "role": "tool",
                "tool_call_id": c.id,
                "content": results.get(c.id, "ok"),
            }
        )
    return out


def _coerce_generic_value(
    slot: Slot,
    raw: object,
) -> tuple[str | list[str] | None, str | None]:
    """``(value, None)`` or ``(None, error)``; the error is shown to the model."""
    if slot.qtype == "free_text":
        text_value = str(raw).strip() if isinstance(raw, str) else ""
        if not text_value:
            return None, "error: free-text answer is empty. Ask the user and try again."
        return text_value, None

    if slot.qtype == "single_select":
        if not isinstance(raw, str) or raw not in slot.options:
            return None, (
                f"error: '{raw}' is not an option for this question. Pick exactly "
                f"one of: {list(slot.options)}."
            )
        return raw, None

    if not isinstance(raw, list):
        return None, (
            f"error: this question is multi-select — pass an array of option "
            f"labels from: {list(slot.options)}."
        )
    chosen: list[str] = []
    rejected: list[object] = []
    for item in cast(list[object], raw):
        if isinstance(item, str) and item in slot.options:
            if item not in chosen:
                chosen.append(item)
        else:
            rejected.append(item)
    if not chosen:
        return None, (
            f"error: no valid options (rejected: {rejected}). Pick from: {list(slot.options)}."
        )
    if slot.max_select is not None and len(chosen) > slot.max_select:
        return None, (
            f"error: too many options ({len(chosen)}); at most {slot.max_select} "
            "allowed. Ask the user to narrow their choice."
        )
    return chosen, None


def _clean_infra_types(raw: object) -> tuple[list[str], list[str]]:
    """``(known types de-duplicated in order, rejected strings)``; non-strings are dropped."""
    ordered: list[str] = []
    rejected: list[str] = []
    if isinstance(raw, list):
        for v in cast(list[object], raw):
            if not isinstance(v, str):
                continue
            if v in _INFRA_TYPE_VALUES:
                if v not in ordered:
                    ordered.append(v)
            else:
                rejected.append(v)
    return ordered, rejected


def _missing_fields(session: Session) -> list[str]:
    return missing_fields(session, plan_for_session(session))


def draft_complete(session: Session) -> bool:
    return not _missing_fields(session)


class ConversationFlow:
    def __init__(
        self,
        *,
        provider: Provider,
        sessions: SessionStore,
        crises: CrisisService,
        llm: LLMClient,
        submitter: ReportSubmitter,
    ) -> None:
        self._provider = provider
        self._sessions = sessions
        self._crises = crises
        self._llm = llm
        self._submitter = submitter

    async def handle(self, msg: InboundMessage) -> None:
        _logger.warning(
            "whatsapp.flow.handle from=%s body=%r has_media=%s has_pin=%s has_flow=%s",
            msg.from_e164,
            msg.body,
            bool(msg.num_media),
            msg.latitude is not None,
            msg.flow_response is not None,
        )
        await self._sessions.gc()
        session = await self._sessions.get(msg.from_e164)

        # Flow completion: fill the draft, ask for a location pin, skip the LLM.
        if msg.flow_response is not None:
            if session is None:
                session = Session(phone_e164=msg.from_e164)
                await self._bootstrap(session)
            await self._handle_flow_complete(session, msg.flow_response)
            await self._sessions.upsert(session)
            return

        if (
            session is not None
            and session.scratch.get("origin") == "flow"
            and msg.latitude is not None
            and msg.longitude is not None
        ):
            session.location = (msg.latitude, msg.longitude)
            if draft_complete(session):
                ok, _ = await self._do_submit(session)
                if ok:
                    await self._sessions.delete(session.phone_e164)
                    return
            else:
                _logger.warning(
                    "whatsapp.flow.flow_origin_submit_incomplete from=%s",
                    msg.from_e164,
                )
            await self._sessions.upsert(session)
            return

        if session is None or parse_reset(msg.body):
            if session is not None:
                await self._sessions.delete(session.phone_e164)
            session = Session(phone_e164=msg.from_e164)
            await self._bootstrap(session)

        # Pins and photos reach the LLM only as markers in history.
        captured = self._capture_inbound(session, msg)

        rendered = self._render_inbound(msg, captured)
        if rendered:
            session.append_history(HistoryMsg(role="user", content=rendered))

        _logger.warning(
            "whatsapp.flow.calling_llm history_len=%d crisis_set=%s",
            len(session.history),
            session.crisis_id is not None,
        )
        result = await self._llm.run_turn(session)
        _logger.warning(
            "whatsapp.flow.llm_result reply_len=%d reply=%r tool_calls=%d error=%s",
            len(result.reply_text),
            result.reply_text[:200],
            len(result.tool_calls),
            result.error,
        )

        terminate, tool_results = await self._apply_tools(session, result.tool_calls)
        reply_text = result.reply_text

        # OpenAI tool-use loop: the next call must carry the assistant's tool-call
        # message and a `tool` result per tool_call_id, or the model just emits
        # another tool call.
        trailing: list[dict[str, object]] = []
        prev = result
        prev_results = tool_results
        for _hop in range(_MAX_TOOL_HOPS):
            if terminate or reply_text or not prev.tool_calls:
                break
            trailing.extend(_assistant_tool_call_msgs(prev.tool_calls, prev_results))
            _logger.warning(
                "whatsapp.flow.tooluse_continue tools=%s results=%s",
                [c.name for c in prev.tool_calls],
                [prev_results.get(c.id, "ok")[:80] for c in prev.tool_calls],
            )
            prev = await self._llm.run_turn(session, trailing=trailing)
            _logger.warning(
                "whatsapp.flow.tooluse_result reply_len=%d tool_calls=%d",
                len(prev.reply_text),
                len(prev.tool_calls),
            )
            reply_text = prev.reply_text
            hop_terminate, prev_results = await self._apply_tools(session, prev.tool_calls)
            terminate = hop_terminate or terminate

        # Reply anyway, so a provider misconfig isn't silence.
        outbound = reply_text
        if not outbound and result.error:
            outbound = strings_for(normalize_lang(session.language)).flow_failed
        if outbound:
            _logger.warning(
                "whatsapp.flow.sending to=%s body=%r",
                session.phone_e164,
                outbound[:200],
            )
            try:
                await self._provider.send_text(session.phone_e164, outbound)
            except Exception:
                _logger.exception("whatsapp.flow.send_text_failed")
                raise
            if reply_text:
                session.append_history(HistoryMsg(role="assistant", content=reply_text))
        else:
            _logger.warning("whatsapp.flow.nothing_to_send")

        if terminate:
            await self._sessions.delete(session.phone_e164)
        else:
            await self._sessions.upsert(session)

    async def _bootstrap(self, session: Session) -> None:
        crises = await self._crises.list_active()
        if len(crises) > _MAX_CRISIS_CHOICES:
            # Keep the last row: the reserved "Other / Unspecified" crisis sorts last.
            ordered = [*crises[: _MAX_CRISIS_CHOICES - 1], crises[-1]]
        else:
            ordered = list(crises)
        session.active_crisis_choices = {c.name: str(c.id) for c in ordered}
        session.state = "active"

    async def _load_form(self, session: Session) -> None:
        """Pin the crisis form schema and version; on failure the default schema is used."""
        if session.crisis_id is None:
            return
        try:
            form = await self._crises.get_form(session.crisis_id)
        except Exception:
            _logger.exception("whatsapp.flow.load_form_failed crisis_id=%s", session.crisis_id)
            return
        if form is None:
            _logger.warning(
                "whatsapp.flow.load_form_missing crisis_id=%s, using default schema",
                session.crisis_id,
            )
            return
        session.form_schema, session.form_version = form
        plan = plan_for_session(session)
        slots = [s.target if s.is_generic else s.kind for s in plan]
        # WARNING so it shows in the uvicorn console, which filters INFO.
        _logger.warning(
            "whatsapp.flow.form_loaded crisis_id=%s form_version=%s slots=%s",
            session.crisis_id,
            session.form_version,
            slots,
        )

    def _capture_inbound(self, session: Session, msg: InboundMessage) -> dict[str, bool]:
        captured = {"location": False, "photo": False, "photo_invalid": False}
        if msg.latitude is not None and msg.longitude is not None:
            session.location = (msg.latitude, msg.longitude)
            captured["location"] = True
        if msg.num_media:
            outcome = capture_photo(session, msg)
            captured["photo"] = outcome == "stored"
            captured["photo_invalid"] = outcome == "invalid"
        return captured

    def _render_inbound(self, msg: InboundMessage, captured: dict[str, bool]) -> str:
        parts: list[str] = []
        body = (msg.body or "").strip()
        if body:
            parts.append(body)
        if captured["location"]:
            parts.append("[shared location pin]")
        if captured["photo"]:
            parts.append("[sent photo]")
        if captured["photo_invalid"]:
            parts.append("[sent a non-image file]")
        return " ".join(parts)

    async def _apply_tools(
        self, session: Session, calls: list[ToolCall]
    ) -> tuple[bool, dict[str, str]]:
        """``(terminate, results)``; results map tool_call_id to the status echoed to the model."""
        results: dict[str, str] = {}
        generic_slots = {s.target: s for s in plan_for_session(session) if s.is_generic}
        for call in calls:
            name = call.name
            args = call.arguments
            results[call.id] = "ok"
            if name == "set_crisis":
                proposed = str(args.get("name", "")).strip()
                cid = session.active_crisis_choices.get(proposed)
                if cid is not None:
                    session.crisis_id = uuid.UUID(cid)
                    session.crisis_name = proposed
                    await self._load_form(session)
                else:
                    _logger.warning(
                        "whatsapp.flow.invalid_set_crisis",
                        extra={"proposed_name": proposed},
                    )
                    valid = sorted(session.active_crisis_choices.keys())
                    results[call.id] = (
                        f"error: '{proposed}' is not in active_crisis_names. "
                        f"Valid options: {valid}. Ask the user to pick one of these."
                    )
            elif name == "set_damage_class":
                value = str(args.get("value", ""))
                if is_damage_class(value):
                    session.damage_class = value
                else:
                    _logger.warning(
                        "whatsapp.flow.invalid_set_damage_class",
                        extra={"value": value},
                    )
                    results[call.id] = (
                        f"error: '{value}' is not a valid damage_class. "
                        f"Must be one of {list(DAMAGE_VALUES)}. Map the "
                        "user's wording to one of these English values "
                        "(do NOT translate — pass the English label) and "
                        "call set_damage_class again."
                    )
            elif name == "set_infra_description":
                value = str(args.get("value", "")).strip()
                if value:
                    session.infra_description = value
            elif name == "set_route_description":
                value = str(args.get("value", "")).strip()
                if value:
                    session.route_description = value[:MAX_ROUTE_CHARS]
            elif name == "set_debris":
                value = str(args.get("value", ""))
                if is_debris(value):
                    session.debris = value
                else:
                    _logger.warning(
                        "whatsapp.flow.invalid_set_debris",
                        extra={"value": value},
                    )
                    results[call.id] = (
                        f"error: '{value}' is not a valid debris value. "
                        f"Must be one of {list(DEBRIS_VALUES)}. Pass the "
                        "English value (do NOT translate) and call "
                        "set_debris again."
                    )
            elif name == "set_infra_type":
                ordered, rejected = _clean_infra_types(args.get("value"))
                if ordered:
                    session.infra_type = ordered
                other = str(args.get("other_description", "")).strip()
                if other:
                    session.infra_type_other = other
                if not ordered:
                    _logger.warning(
                        "whatsapp.flow.invalid_set_infra_type rejected=%s",
                        rejected,
                    )
                    results[call.id] = (
                        f"error: no valid infra_type values (rejected: {rejected}). "
                        f"Must be a non-empty subset of "
                        f"{sorted(_INFRA_TYPE_VALUES)}. Pass the English "
                        "labels (do NOT translate) and call set_infra_type "
                        "again."
                    )
            elif name == "set_crisis_nature":
                value = str(args.get("value", "")).strip()
                if value in _CRISIS_NATURE_VALUES:
                    session.crisis_nature = value
                else:
                    _logger.warning(
                        "whatsapp.flow.invalid_set_crisis_nature",
                        extra={"value": value},
                    )
                    results[call.id] = (
                        f"error: '{value}' is not a valid crisis_nature. "
                        f"Must be one of {sorted(_CRISIS_NATURE_VALUES)}. "
                        "Pass the English label (do NOT translate, e.g. "
                        "'نزاع' -> 'Conflict', 'زلزال' -> 'Earthquake') "
                        "and call set_crisis_nature again."
                    )
                other = str(args.get("other_description", "")).strip()
                if other:
                    session.crisis_nature_other = other
            elif name == "set_language":
                value = str(args.get("value", ""))
                if value in SUPPORTED_LANGS:
                    session.language = value
            elif name == "set_generic_answer":
                label = str(args.get("question_label", "")).strip()
                slot = generic_slots.get(label)
                if slot is None:
                    _logger.warning(
                        "whatsapp.flow.invalid_generic_question",
                        extra={"label": label},
                    )
                    valid = sorted(generic_slots)
                    results[call.id] = (
                        f"error: '{label}' is not a known question. Valid "
                        f"`question_label` values: {valid}. Use the EXACT "
                        "`question` text from `generic_questions`."
                    )
                else:
                    coerced, err = _coerce_generic_value(slot, args.get("value"))
                    if err is not None:
                        _logger.warning(
                            "whatsapp.flow.invalid_generic_answer label=%s err=%s",
                            label,
                            err,
                        )
                        results[call.id] = err
                    else:
                        assert coerced is not None
                        session.generic_answers[label] = coerced
            elif name == "submit_report":
                missing = _missing_fields(session)
                if missing:
                    _logger.warning(
                        "whatsapp.flow.submit_rejected_incomplete",
                        extra={"missing": missing},
                    )
                    results[call.id] = (
                        "error: submission rejected — these required fields "
                        f"are still unset: {missing}. Do NOT tell the user "
                        "the report was submitted. Ask for the missing "
                        "fields (one per turn) and call the matching set_* "
                        "tool with the answer, then call submit_report again."
                    )
                    continue
                ok, submit_status = await self._do_submit(session)
                results[call.id] = submit_status
                if ok:
                    return True, results
            elif name == "cancel":
                session.state = "done"
                return True, results
            else:
                _logger.info("whatsapp.flow.unknown_tool", extra={"tool": name})
                results[call.id] = f"error: unknown tool '{name}'"
        return False, results

    async def _do_submit(self, session: Session) -> tuple[bool, str]:
        """``(terminate, status)``; a failure status says so, so the model can't claim success."""
        s = strings_for(normalize_lang(session.language))
        try:
            report_id = await self._submitter.submit(session)
        except Exception as exc:
            _logger.exception("whatsapp.flow.submit_failed")
            # Keep the session so the LLM can offer a retry.
            session.append_history(
                HistoryMsg(role="user", content="[submission failed]"),
            )
            return False, (
                f"error: submission failed ({type(exc).__name__}). Do NOT "
                "tell the user the report was submitted. Apologize, briefly "
                "explain there was a server error, and offer to retry."
            )
        ref = str(report_id)[:8]
        body = s.submitted.format(ref=ref)
        await self._provider.send_text(session.phone_e164, body)
        session.state = "done"
        return True, f"ok: report submitted with reference {ref}"

    # ---- WhatsApp Flow ingestion ----------------------------------------

    async def _handle_flow_complete(
        self,
        session: Session,
        payload: dict[str, object],
    ) -> None:
        """Fill the draft from a Flow completion payload and ask for a location pin.

        Field names mirror `flows/report_v1.json`. Missing or malformed
        fields are tolerated — the location step + `draft_complete`
        guard catch under-filled drafts before submit.
        """
        _logger.warning("whatsapp.flow.flow_complete payload_keys=%s", list(payload))

        session.scratch["origin"] = "flow"

        def _s(key: str) -> str | None:
            v = payload.get(key)
            return v if isinstance(v, str) and v else None

        damage = _s("damage_class")
        if is_damage_class(damage):
            session.damage_class = damage

        infra_desc = _s("infra_description")
        if infra_desc:
            session.infra_description = infra_desc

        debris = _s("debris")
        if is_debris(debris):
            session.debris = debris

        ordered, _ = _clean_infra_types(payload.get("infra_type"))
        if ordered:
            session.infra_type = ordered

        infra_other = _s("infra_type_other")
        if infra_other:
            session.infra_type_other = infra_other

        nature = _s("crisis_nature")
        if nature in _CRISIS_NATURE_VALUES:
            session.crisis_nature = nature
        nature_other = _s("crisis_nature_other")
        if nature_other:
            session.crisis_nature_other = nature_other

        # PhotoPicker values: best-effort base64 decode; CDN-reference dicts are not handled.
        photo_bytes = _try_decode_flow_photo(payload.get("photo"))
        if photo_bytes is not None:
            session.photo_bytes = photo_bytes
            session.photo_mime = "image/jpeg"

        # Flows do not pick a crisis; use the reserved one if it is active.
        if session.crisis_id is None:
            reserved = get_settings().reserved_crisis_name
            cid = session.active_crisis_choices.get(reserved)
            if cid is not None:
                session.crisis_id = uuid.UUID(cid)
                session.crisis_name = reserved
                await self._load_form(session)
            else:
                _logger.warning(
                    "whatsapp.flow.reserved_crisis_missing reserved=%r choices=%s",
                    reserved,
                    list(session.active_crisis_choices),
                )

        prompt = (
            "Thanks! Last step — please share the location of the damaged "
            "building so we can place it on the map."
        )
        try:
            await self._provider.send_location_request(session.phone_e164, prompt)
        except NotImplementedError:
            await self._provider.send_text(
                session.phone_e164,
                prompt + "\n\nTap 📎 → Location → Send your current location.",
            )
        except Exception:
            _logger.exception("whatsapp.flow.send_location_request_failed")
            raise


def _try_decode_flow_photo(raw: object) -> bytes | None:
    """Best-effort base64 decode of a Flow PhotoPicker value.

    Accepts the shapes we've seen documented: a list of base64 strings
    (optionally prefixed with `data:image/...;base64,`) or a list of
    dicts with a `cdn_url` key (which we skip — fetching CDN media needs
    a separate Bearer call we don't implement yet). Returns None when
    the shape is unrecognised.
    """
    if not isinstance(raw, list) or not raw:
        return None
    raw_list: list[object] = raw  # pyright: ignore[reportUnknownVariableType]
    first: object = raw_list[0]
    if isinstance(first, str):
        s = first
        if s.startswith("data:") and ";base64," in s:
            s = s.split(";base64,", 1)[1]
        try:
            return base64.b64decode(s, validate=False)
        except (ValueError, TypeError):
            return None
    return None
