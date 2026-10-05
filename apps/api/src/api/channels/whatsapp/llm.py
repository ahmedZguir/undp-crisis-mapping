"""LLM client for the WhatsApp bot.

Each turn sends the system prompt, a draft snapshot, bounded history and the
latest message, then the flow applies the returned tool calls. The LLM never
sees coordinates and can only pick a crisis by an exact name from the snapshot.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol, cast

from openai import AsyncAzureOpenAI

from api.channels.languages import SUPPORTED_LANGS
from api.channels.plan import Slot, missing_fields, plan_for_session
from api.channels.sessions import HistoryMsg, Session
from api.channels.values import (
    DAMAGE_VALUES,
    DEBRIS_VALUES,
    INFRA_TYPES,
    NATURE_LABELS,
    NATURE_OTHER,
)

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class TurnResult:
    reply_text: str
    tool_calls: list[ToolCall]
    # Set only when the API call raised.
    error: str | None = None


_DAMAGE_VALUES: tuple[str, ...] = DAMAGE_VALUES
_DEBRIS_VALUES: tuple[str, ...] = DEBRIS_VALUES
_INFRA_TYPE_VALUES: tuple[str, ...] = INFRA_TYPES
_CRISIS_NATURE_VALUES: tuple[str, ...] = (*NATURE_LABELS, NATURE_OTHER)


_SYSTEM_PROMPT_INTRO = """\
You are the conversational driver for a WhatsApp damage-report assistant
on a UN crisis-mapping platform. The user is an affected resident
describing damage to a building or piece of infrastructure. You own the
entire conversation. The bot you sit behind only handles I/O — sending
your replies, capturing location pins, capturing photo uploads,
validating crisis names, and submitting the final report.

Language policy — critical:
- Reply in the SAME language the user is writing in. If they write in
  Arabic, reply in Arabic. If they write in English, reply in English.
- On the first user turn, detect their language and call
  `set_language(value=...)` with the matching BCP-47 short tag from
  the supported set. This is just for server-side bookkeeping; you
  still always mirror the user's language regardless.
- If the user explicitly asks to switch language, call `set_language`
  again and continue in the new language.
"""

# The fields block between intro and tail is generated per turn from the
# crisis form schema.

_SYSTEM_PROMPT_TAIL = """\
Walk the user through the steps one at a time. Do not ask for multiple
fields in one message except for photo + damage_class (step 2), which
share a screen in the PWA.

Tool-call discipline — ABSOLUTE RULE:
- The draft snapshot system message is the ONLY source of truth for
  what the server has recorded. Values mentioned in chat history but
  not present in the snapshot DO NOT EXIST as far as submission is
  concerned.
- Every time the user answers a field you are collecting, you MUST call
  the matching tool in the SAME reply that acknowledges them — `set_*`
  for the built-in fields (damage_class, infra_description, debris,
  infra_type, crisis_nature, language) and `set_generic_answer` for any
  admin-authored question listed in `generic_questions`. Acknowledging
  in prose without the tool call is a bug — the value is lost.
- Before calling `submit_report`, look at `fields_remaining` in the
  draft snapshot. If it is NON-EMPTY, DO NOT call `submit_report`.
  Either ask the user for a listed item, or — if you already know the
  answer from chat history — call the matching `set_*` /
  `set_generic_answer` tool first in the same reply, then call
  `submit_report`.
- If the server returns an error result for `submit_report` listing
  missing fields, DO NOT tell the user the report was submitted. Re-
  record the missing fields with their tools (using values from chat
  history if you have them, otherwise ask) and call `submit_report`
  again.

Opening turn:
- The user's first message arrives with no prior bot greeting. Greet
  them warmly in their language. Briefly explain the service helps
  report building damage so responders can reach it. Then ask which
  crisis they're reporting about. Always list the active crisis names
  from the draft snapshot in your prose reply so they can see the
  options.

Crisis selection — hard rule:
- You NEVER set a crisis_id directly. Call `set_crisis(name=...)` only
  with a name that appears EXACTLY in `active_crisis_names` from the
  draft snapshot. The bot rejects anything else.
- Before calling `set_crisis`, confirm the match with the user in prose
  ("Did you mean <name>?" in their language). Only call `set_crisis`
  once they say yes.
- If the user's input doesn't match any active crisis, list the active
  options again in prose and ask them to pick.

Location and photo:
- You NEVER produce coordinates. Ask the user (in their language) to
  share a WhatsApp LOCATION PIN — the paperclip icon, then Location. A
  pin is NOT mandatory: if the user can't or won't share one, ask them
  to describe how to find the building in words (landmarks, street,
  neighbourhood) and record it with `set_route_description`. They can
  also send a VOICE NOTE 🎙️ instead of typing directions — the bot
  will transcribe it automatically. A report needs a pin OR written/
  spoken directions — not both. Once you have either, do not keep
  pressing for the pin.
- Ask for a single PHOTO of the damage. When the numbered field list
  marks the photo OPTIONAL, say so in the SAME message you ask for it:
  tell the user they can skip the photo and instead describe the damage
  in words or by sending a VOICE NOTE 🎙️ — the bot transcribes voice
  notes automatically — and you record the result with
  `set_infra_description`. Then a report needs a photo OR a description
  — not both; once you have either, do not keep pressing for the photo.
  (When the list marks the photo REQUIRED, do not offer this — a photo
  is mandatory for that crisis.)
- When the user shares a pin or sends a photo, the bot captures it and
  surfaces `has_location: true` / `has_photo: true` in the snapshot on
  the next turn. Acknowledge and move to the next missing field.

Damage class:
- You NEVER fabricate a damage_class value outside the closed set
  (minimal | partial | complete). Map free-form severity to it:
  - "everything fell down", "collapsed", "gone", "انهار" => complete
  - "wall cracks but standing", "some damage", "تشققات" => partial
  - "small chips", "barely anything", "خدش بسيط" => minimal
  If unclear, ask a clarifying question (no tool call).

Closed-set fields — never fabricate values:
- debris must be exactly `yes`, `no`, or `unknown`. Map free-form
  answers to that set; if the user can't tell, use `unknown`.
- infra_type must be a non-empty subset of the eight values above.
  Translate the user's words (e.g. "school" => `community`, "road" =>
  `transport`, "house" => `residential`). When the user explicitly
  says "other" or none of the eight fits, pass `other`; the free-text
  `other_description` is OPTIONAL — invite it but tell the user they can
  skip it, and never block on it. Pass it only if they actually give
  wording (preserve their language).
- crisis_nature must be exactly one of the eleven canonical English
  labels above. Translate the user's wording into one of those labels
  (e.g. "زلزال" => `Earthquake`, "fire" => `Wildfire`, "fighting" =>
  `Conflict`, "حريق" => `Wildfire`). Use the English label even if the
  conversation is in Arabic. When `Other`, the `other_description` is
  OPTIONAL — invite it but tell the user they can skip it; pass it only
  if they supply wording.

ALWAYS list the available options in prose when asking the closed-set
fields, in the user's language, so they know what to pick. For
infra_type, list all eight options. For crisis_nature, list all eleven.

Admin-authored questions:
- Some crises add extra questions, shown in `generic_questions` in the
  draft snapshot with their `type`, `options`, `required` and
  `max_select`. They appear in the ordered field list above. Ask each in
  the user's language and list its options. Record the answer with
  `set_generic_answer`, passing the EXACT `question` text as
  `question_label` and the EXACT option label(s) shown — never translate
  or invent option labels. For `multi_select` pass an array and respect
  `max_select`; for `free_text` pass the user's text.

Submission:
- Do NOT move toward submission until you have worked through EVERY item in
  the ordered list above (optional ones included — the user may decline) AND
  `fields_remaining` is empty. `fields_remaining` empty alone is not enough
  if list items are still unasked.
- Once every item has been addressed and `fields_remaining` is empty,
  summarize the draft in prose in the user's language (crisis name, damage
  class, description if any, debris answer, infra_type selections,
  crisis_nature, any answers to admin-authored questions, "location pin
  shared", "photo received") and ask them to confirm submission.
- On their explicit "yes / submit / correct / نعم" call
  `submit_report()` with a brief prose acknowledgement. The bot will
  send the user their reference code.
- If they want to change something, just continue the conversation —
  ask what to fix, then re-record with the appropriate `set_*` tool.

Cancel:
- "cancel / stop / إلغاء" at any time => `cancel()` with a brief
  goodbye in their language.

Style:
- One concise reply per turn, under two sentences (longer only for the
  pre-submit summary).
- ALWAYS include a prose reply alongside any tool call — the user only
  sees your prose. A tool call with no reply leaves them staring at
  silence.
- Warm but efficient. The user is stressed.

Trust the draft-snapshot system message — it reflects current
server-side truth and overrides anything you may have inferred from
earlier turns.
"""


# Options must match those enforced in flow.py; the schema only picks and orders fields.
_BUILTIN_FIELD_LINES: dict[str, str] = {
    "infra_description": (
        "infra_description (OPTIONAL free-text describing the building — the user "
        "may type it OR send a voice note 🎙️, which the bot transcribes automatically)"
    ),
    "debris": 'debris (one of: yes | no | unknown — "is there debris blocking access?")',
    "infra_type": (
        "infra_type (multi-select, AT LEAST ONE of: residential, commercial, "
        "government, utility, transport, community, public_spaces, other — if "
        "the user picks `other` you MAY collect an OPTIONAL free-text "
        "description, telling them they can skip it)"
    ),
    "crisis_nature": (
        "crisis_nature (single value, one of: Earthquake, Flood, Tsunami, "
        "Hurricane, Landslide, Wildfire, Explosion, Chemical incident, "
        "Conflict, Civil unrest, Other — if `Other`, you MAY collect an "
        "OPTIONAL free-text description, telling them they can skip it)"
    ),
    "location": (
        "location (a WhatsApp location PIN — you must never invent coordinates; "
        "the bot captures the pin when the user shares it. Ask them to tap 📎 → "
        "Location. A pin is NOT mandatory: if the user can't or won't share one, "
        "ask them to describe how to reach the building in words — landmarks, "
        "street, neighbourhood — or send a VOICE NOTE 🎙️ (the bot transcribes "
        "it automatically) — and record that with set_route_description. A "
        "report needs a pin OR written/spoken directions, not both. Once you "
        "have either, move on)"
    ),
}


# Makes the model offer a skip for optional fields, like the other channels do.
_OPTIONAL_SUFFIX = (
    " — OPTIONAL: when you ask, tell the user they can skip it; if they decline, move on."
)


def _slot_line(slot: Slot) -> str:
    if not slot.is_generic:
        line = _BUILTIN_FIELD_LINES.get(slot.target, slot.target)
        return line if slot.required else line + _OPTIONAL_SUFFIX
    opts = " / ".join(slot.options)
    label = slot.target
    if slot.qtype == "free_text":
        req = "required" if slot.required else "optional"
        return (
            f'ask "{label}" ({req} free text). Record with '
            f'set_generic_answer(question_label="{label}", value="<their answer>").'
        )
    if slot.qtype == "single_select":
        line = (
            f'ask "{label}" — pick ONE of: {opts}. Record with '
            f'set_generic_answer(question_label="{label}", value="<exact option label>").'
        )
        return line if slot.required else line + _OPTIONAL_SUFFIX
    cap = f", at most {slot.max_select}" if slot.max_select else ""
    at_least = "at least one" if slot.required else "any number"
    line = (
        f'ask "{label}" — pick {at_least} of: {opts}{cap}. Record with '
        f'set_generic_answer(question_label="{label}", value=["<exact option labels>"]).'
    )
    return line if slot.required else line + _OPTIONAL_SUFFIX


def _render_fields_block(plan: list[Slot]) -> str:
    # A description can replace the photo only when its page is enabled,
    # since the submitter forwards it only then.
    desc_enabled = any(not slot.is_generic and slot.kind == "description" for slot in plan)
    if desc_enabled:
        photo_line = (
            "2. photo + damage_class (one image of the damage AND severity: "
            "minimal | partial | complete — ask for both on the same turn. "
            "damage_class is always required, but the PHOTO is OPTIONAL: when "
            "you ask, tell the user up front they can skip it and instead "
            "describe the damage in words or by sending a voice note 🎙️ (the "
            "bot transcribes voice notes automatically), which you record with "
            "set_infra_description)"
        )
    else:
        photo_line = (
            "2. photo + damage_class (one image of the damage AND severity: "
            "minimal | partial | complete — ask for both on the same turn; a "
            "photo is REQUIRED for this crisis and damage_class is always "
            "required)"
        )
    lines = [
        "1. crisis (which crisis the report belongs to — pick from the active "
        "list in the draft snapshot; ask FIRST, before anything else)",
        photo_line,
    ]
    lines.extend(f"{n}. {_slot_line(slot)}" for n, slot in enumerate(plan, start=3))
    return "\n".join(lines)


def build_system_prompt(plan: list[Slot]) -> str:
    fields = _render_fields_block(plan)
    return (
        f"{_SYSTEM_PROMPT_INTRO}\n"
        "Fields to collect, IN THIS ORDER (these are configured per crisis — "
        "ask ONLY what is listed here, in this order; if a field such as "
        "debris is absent, do NOT ask about it):\n\n"
        f"{fields}\n\n"
        "Ask the items in this list ONE AT A TIME, strictly in the order shown "
        "— including the OPTIONAL ones, and including the location pin at "
        "whatever position it appears (do NOT save it for last unless it is "
        "listed last). 'Optional' means the USER may decline to answer: still "
        "ASK, but in the same message tell them they can skip it, and if they "
        "decline just move on. It does NOT mean you skip asking. Never jump ahead or "
        "defer an item. `fields_remaining` in the snapshot lists only what "
        "BLOCKS submission — it is not your to-ask list; walk the numbered list "
        "above instead.\n\n"
        f"{_SYSTEM_PROMPT_TAIL}"
    )


_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "set_crisis",
            "description": (
                "Lock in the crisis FK. Pass the EXACT crisis name from "
                "`active_crisis_names` in the draft snapshot. Only call "
                "after the user has confirmed the match in prose."
            ),
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_damage_class",
            "description": "Record the damage severity from the closed set.",
            "parameters": {
                "type": "object",
                "properties": {
                    "value": {"type": "string", "enum": list(_DAMAGE_VALUES)},
                },
                "required": ["value"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_infra_description",
            "description": (
                "Record the user's free-text description of the damaged "
                "structure (PWA StepDescription / Flow DESCRIPTION screen). "
                "Preserve their wording and language."
            ),
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_route_description",
            "description": (
                "Record free-text directions to the damaged building when the "
                "user shares no GPS pin (landmarks, street, neighbourhood). The "
                "other half of the 'location OR route' gate. Preserve the "
                "user's wording and language."
            ),
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_debris",
            "description": (
                "Record whether debris is blocking access to the building. "
                "Closed set — `yes`, `no`, or `unknown`. Map free-form "
                "user answers; use `unknown` if the user is unsure."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "value": {"type": "string", "enum": list(_DEBRIS_VALUES)},
                },
                "required": ["value"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_infra_type",
            "description": (
                "Record the type(s) of infrastructure affected. "
                "Multi-select from the closed set; at least one value. "
                "Pass `other_description` only when `other` is in `value` "
                "and the user has provided wording — preserve their "
                "language."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "value": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "enum": list(_INFRA_TYPE_VALUES)},
                    },
                    "other_description": {"type": "string"},
                },
                "required": ["value"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_crisis_nature",
            "description": (
                "Record the crisis nature. Closed set of canonical "
                "English labels — pass `value` as one of those labels "
                "even when the conversation is in another language. "
                "Pass `other_description` only when `value == 'Other'` "
                "and the user has supplied wording — preserve their "
                "language."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "value": {"type": "string", "enum": list(_CRISIS_NATURE_VALUES)},
                    "other_description": {"type": "string"},
                },
                "required": ["value"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_language",
            "description": "Record the user's preferred reply language (BCP-47 short tag).",
            "parameters": {
                "type": "object",
                "properties": {
                    "value": {"type": "string", "enum": list(SUPPORTED_LANGS)},
                },
                "required": ["value"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_generic_answer",
            "description": (
                "Record the answer to one admin-authored question listed in "
                "`generic_questions` in the draft snapshot. Pass the EXACT "
                "`question` string as `question_label`. For a single_select "
                "question pass the chosen option label as a string; for "
                "multi_select pass an array of option labels (respect "
                "`max_select`); for free_text pass the user's text. Use ONLY "
                "the option labels shown for that question — never translate "
                "or invent options."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question_label": {"type": "string"},
                    "value": {
                        "type": ["string", "array"],
                        "items": {"type": "string"},
                    },
                },
                "required": ["question_label", "value"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_report",
            "description": (
                "Submit the completed report. Only call after the user "
                "has explicitly confirmed the pre-submit summary. The "
                "bot rejects submission if any required field is unset."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel",
            "description": "User wants to cancel the in-progress report.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
]


def _draft_snapshot(session: Session, plan: list[Slot]) -> str:
    crisis_names = sorted(session.active_crisis_choices.keys())
    generic_questions = [
        {
            "question": slot.target,
            "type": slot.qtype,
            "options": list(slot.options),
            "max_select": slot.max_select,
            "required": slot.required,
            "answered": slot.target in session.generic_answers,
        }
        for slot in plan
        if slot.is_generic
    ]
    snapshot = {
        "crisis_name": session.crisis_name,
        "has_photo": session.has_photo,
        "damage_class": session.damage_class,
        "infra_description": session.infra_description,
        "debris": session.debris,
        "infra_type": session.infra_type,
        "infra_type_other": session.infra_type_other,
        "crisis_nature": session.crisis_nature,
        "crisis_nature_other": session.crisis_nature_other,
        "has_location": session.location is not None,
        "route_description": session.route_description,
        "language": session.language,
        "active_crisis_names": crisis_names,
        "generic_questions": generic_questions,
        "generic_answers": session.generic_answers,
        "fields_remaining": missing_fields(session, plan),
    }
    return "Draft snapshot:\n" + json.dumps(snapshot, ensure_ascii=False, indent=2)


def _history_to_openai(history: list[HistoryMsg]) -> list[dict[str, Any]]:
    # Prose only: replaying tool calls would require matching tool responses.
    out: list[dict[str, Any]] = []
    for h in history:
        if h.role in ("user", "assistant") and h.content:
            out.append({"role": h.role, "content": h.content})
    return out


class LLMClient(Protocol):
    async def run_turn(
        self,
        session: Session,
        *,
        trailing: list[dict[str, Any]] | None = None,
    ) -> TurnResult: ...


class AzureOpenAILLMClient:
    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str,
        api_version: str,
        deployment: str,
    ) -> None:
        self._client = AsyncAzureOpenAI(
            api_key=api_key,
            azure_endpoint=endpoint,
            api_version=api_version,
        )
        self._deployment = deployment

    async def run_turn(
        self,
        session: Session,
        *,
        trailing: list[dict[str, Any]] | None = None,
    ) -> TurnResult:
        plan = plan_for_session(session)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": build_system_prompt(plan)},
            {"role": "system", "content": _draft_snapshot(session, plan)},
        ]
        messages.extend(_history_to_openai(session.history))
        if trailing:
            messages.extend(trailing)

        try:
            response = await self._client.chat.completions.create(
                model=self._deployment,
                max_tokens=512,
                temperature=0.2,
                messages=cast(Any, messages),
                tools=cast(Any, _TOOLS),
                tool_choice="auto",
            )
        except Exception as exc:
            _logger.warning(
                "whatsapp.llm.call_failed state=%s error=%r",
                session.state,
                exc,
                exc_info=True,
            )
            return TurnResult(
                reply_text="",
                tool_calls=[],
                error=f"{type(exc).__name__}: {exc}",
            )

        choices = response.choices
        if not choices:
            _logger.warning("whatsapp.llm.empty_choices")
            return TurnResult(reply_text="", tool_calls=[])
        message = choices[0].message
        reply_text = (message.content or "").strip()
        if not reply_text and not message.tool_calls:
            _logger.warning(
                "whatsapp.llm.empty_response finish_reason=%s",
                choices[0].finish_reason,
            )
        tool_calls: list[ToolCall] = []
        for tc in message.tool_calls or []:
            fn = getattr(tc, "function", None)
            if fn is None:
                continue
            try:
                parsed_args = json.loads(fn.arguments or "{}")
            except json.JSONDecodeError as exc:
                _logger.warning(
                    "whatsapp.llm.tool_args_invalid_json",
                    extra={"tool": fn.name, "error": str(exc), "raw": fn.arguments},
                )
                continue
            if not isinstance(parsed_args, dict):
                continue
            args: dict[str, Any] = cast(dict[str, Any], parsed_args)
            tool_calls.append(ToolCall(id=tc.id, name=fn.name, arguments=args))
        return TurnResult(reply_text=reply_text, tool_calls=tool_calls)
