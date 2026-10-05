"""Turns a per-crisis ``form_schema`` into the ordered ``Slot``s the channel bots collect.

A built-in slot's ``target``
is the session field it fills (its options stay hardcoded in the channel); a
generic slot's ``target`` is the question label, since ``generic_answers`` is
keyed by label.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from api.core.i18n import resolve_schema_labels
from api.crises.default_form import DEFAULT_FORM_SCHEMA

if TYPE_CHECKING:
    from api.channels.sessions import Session

_GENERIC_TYPES = frozenset({"single_select", "multi_select", "free_text"})

_BUILTIN_FIELD_BY_KIND: dict[str, str] = {
    "description": "infra_description",
    "debris": "debris",
    "infra_type": "infra_type",
    "crisis_nature": "crisis_nature",
}

# Captured right after the crisis is chosen, so never a plan slot.
_OUT_OF_BAND_KINDS = frozenset({"photo_and_damage"})

# The PWA drops these at submit (buildReportData never serializes them), so the bots never ask.
_EXCLUDED_KINDS = frozenset({"electricity", "health_services", "pressing_needs"})


@dataclass(frozen=True)
class Slot:
    target: str
    kind: str
    is_generic: bool = False
    qtype: str | None = None  # single_select | multi_select | free_text (generics)
    options: tuple[str, ...] = ()
    required: bool = False
    max_select: int | None = None


def build_conversation_plan(form_schema: dict[str, Any], locale: str) -> list[Slot]:
    """Enabled pages in schema order; unknown or malformed pages are skipped."""
    resolved = resolve_schema_labels(form_schema, locale)
    pages = resolved.get("pages")
    if not isinstance(pages, list):
        return []

    slots: list[Slot] = []
    for raw_page in cast(list[object], pages):
        if not isinstance(raw_page, dict):
            continue
        page = cast(dict[str, object], raw_page)
        if not page.get("enabled", False):
            continue
        kind = page.get("kind")
        if not isinstance(kind, str):
            continue
        if kind in _OUT_OF_BAND_KINDS or kind in _EXCLUDED_KINDS:
            continue
        if kind == "generic":
            slots.extend(_generic_slots(page))
        elif kind == "location":
            slots.append(Slot(target="location", kind="location", required=True))
        elif kind in _BUILTIN_FIELD_BY_KIND:
            # Absent `required`: description optional, the other selects required.
            required = bool(page.get("required", kind != "description"))
            slots.append(
                Slot(
                    target=_BUILTIN_FIELD_BY_KIND[kind],
                    kind=kind,
                    required=required,
                )
            )
    return slots


def plan_from_schema(form_schema: dict[str, Any] | None, locale: str) -> list[Slot]:
    schema = form_schema if form_schema is not None else DEFAULT_FORM_SCHEMA
    return build_conversation_plan(schema, locale)


def plan_for_session(session: Session) -> list[Slot]:
    return plan_from_schema(session.form_schema, session.language or "en")


def missing_fields(session: Session, plan: list[Slot]) -> list[str]:
    """Required slots still unset, per the minimum-content rule.

    A description stands in for the photo only when its page is enabled, since
    only then is it forwarded.
    """
    missing: list[str] = []
    builtin_kinds = {slot.kind for slot in plan if not slot.is_generic}
    if session.crisis_id is None:
        missing.append("crisis")
    has_description = "description" in builtin_kinds and session.has_description
    if not session.has_photo and not has_description:
        missing.append("photo")
    if session.damage_class is None:
        missing.append("damage_class")

    missing.extend(missing_optional_builtins_and_generics(session, plan))

    has_route = bool(session.route_description and session.route_description.strip())
    if session.location is None and not has_route:
        missing.append("location")
    return missing


def missing_optional_builtins_and_generics(session: Session, plan: list[Slot]) -> list[str]:
    """Enabled-and-required built-in selects, then required generics, still unset."""
    missing: list[str] = []
    required_builtins = {slot.kind for slot in plan if not slot.is_generic and slot.required}
    if "debris" in required_builtins and session.debris is None:
        missing.append("debris")
    if "infra_type" in required_builtins and not session.infra_type:
        missing.append("infra_type")
    if "crisis_nature" in required_builtins and session.crisis_nature is None:
        missing.append("crisis_nature")

    for slot in plan:
        if slot.is_generic and slot.required and not _has_generic_answer(session, slot.target):
            missing.append(slot.target)
    return missing


def _has_generic_answer(session: Session, label: str) -> bool:
    value = session.generic_answers.get(label)
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return bool(value)


def _generic_slots(page: dict[str, object]) -> list[Slot]:
    questions = page.get("questions")
    if not isinstance(questions, list):
        return []
    out: list[Slot] = []
    for raw_q in cast(list[object], questions):
        if not isinstance(raw_q, dict):
            continue
        q = cast(dict[str, object], raw_q)
        label = q.get("label")
        qtype = q.get("type")
        if not isinstance(label, str) or not isinstance(qtype, str) or qtype not in _GENERIC_TYPES:
            continue
        options: list[str] = []
        raw_options = q.get("options")
        if isinstance(raw_options, list):
            for raw_opt in cast(list[object], raw_options):
                if not isinstance(raw_opt, dict):
                    continue
                opt_label = cast(dict[str, object], raw_opt).get("label")
                if isinstance(opt_label, str):
                    options.append(opt_label)
        max_select = q.get("max_select")
        out.append(
            Slot(
                target=label,
                kind="generic",
                is_generic=True,
                qtype=qtype,
                options=tuple(options),
                required=bool(q.get("required", False)),
                max_select=max_select if isinstance(max_select, int) else None,
            )
        )
    return out
