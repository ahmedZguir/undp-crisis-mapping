"""Schema-driven conversation plan: enabled pages only, in admin order, check-in kinds excluded."""

from __future__ import annotations

import uuid
from typing import Any

from api.channels.plan import build_conversation_plan, missing_fields
from api.channels.sessions import Session
from api.crises.default_form import default_form_schema_copy


def _targets(plan: list[Any]) -> list[str]:
    return [slot.target for slot in plan]


def test_default_schema_yields_builtins_in_order_no_generics() -> None:
    plan = build_conversation_plan(default_form_schema_copy(), "en")
    # photo_and_damage is the out-of-band entry; location takes its schema
    # position (index 1, right after photo+damage); check-ins are disabled.
    assert _targets(plan) == [
        "location",
        "infra_description",
        "debris",
        "infra_type",
        "crisis_nature",
    ]
    assert all(not s.is_generic for s in plan)
    # description is optional; the select built-ins + location are required.
    by_target = {s.target: s for s in plan}
    assert by_target["location"].required is True
    assert by_target["infra_description"].required is False
    assert by_target["debris"].required is True
    assert by_target["infra_type"].required is True
    assert by_target["crisis_nature"].required is True


def test_disabled_builtin_is_omitted() -> None:
    schema = default_form_schema_copy()
    for page in schema["pages"]:
        if page["kind"] == "debris":
            page["enabled"] = False
    plan = build_conversation_plan(schema, "en")
    assert "debris" not in _targets(plan)
    assert _targets(plan) == ["location", "infra_description", "infra_type", "crisis_nature"]


def test_builtin_required_flag_read_from_schema() -> None:
    # An enabled built-in select marked required=false is still a slot (still
    # asked), but its slot carries required=False.
    schema = default_form_schema_copy()
    for page in schema["pages"]:
        if page["kind"] == "debris":
            page["required"] = False
    plan = build_conversation_plan(schema, "en")
    by_target = {s.target: s for s in plan}
    assert "debris" in by_target  # still present / asked
    assert by_target["debris"].required is False
    # Sibling selects keep their default (required) when the flag is absent.
    assert by_target["infra_type"].required is True
    assert by_target["crisis_nature"].required is True


def test_pages_are_walked_in_admin_order() -> None:
    schema = default_form_schema_copy()
    # Move crisis_nature ahead of debris (both are reorderable, index >= 2).
    pages = schema["pages"]
    nature = next(p for p in pages if p["kind"] == "crisis_nature")
    pages.remove(nature)
    debris_idx = next(i for i, p in enumerate(pages) if p["kind"] == "debris")
    pages.insert(debris_idx, nature)
    plan = build_conversation_plan(schema, "en")
    assert _targets(plan) == [
        "location",
        "infra_description",
        "crisis_nature",
        "debris",
        "infra_type",
    ]


def test_checkin_kinds_excluded_even_when_enabled() -> None:
    schema = default_form_schema_copy()
    for page in schema["pages"]:
        if page["kind"] in {"electricity", "health_services", "pressing_needs"}:
            page["enabled"] = True
    plan = build_conversation_plan(schema, "en")
    targets = _targets(plan)
    assert "electricity" not in targets
    assert "health_services" not in targets
    assert "pressing_needs" not in targets


def _generic_page(*questions: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "generic",
        "enabled": True,
        "locked": False,
        "title": "Extra questions",
        "questions": list(questions),
    }


def test_generic_questions_expand_to_slots() -> None:
    schema = default_form_schema_copy()
    schema["pages"].append(
        _generic_page(
            {"type": "free_text", "label": "Household size?", "required": True},
            {
                "type": "single_select",
                "label": "Roof intact?",
                "required": False,
                "options": [{"label": "Yes"}, {"label": "No"}],
            },
            {
                "type": "multi_select",
                "label": "Urgent needs?",
                "required": True,
                "max_select": 2,
                "options": [{"label": "Food"}, {"label": "Water"}, {"label": "Shelter"}],
            },
        )
    )
    plan = build_conversation_plan(schema, "en")
    generics = [s for s in plan if s.is_generic]
    assert [s.target for s in generics] == ["Household size?", "Roof intact?", "Urgent needs?"]

    free, single, multi = generics
    assert free.qtype == "free_text" and free.required is True and free.options == ()
    assert single.qtype == "single_select" and single.options == ("Yes", "No")
    assert single.required is False
    assert multi.qtype == "multi_select" and multi.options == ("Food", "Water", "Shelter")
    assert multi.required is True and multi.max_select == 2


def test_generic_with_many_long_options_carried_verbatim() -> None:
    # The plan does not cap option count or label length — interactive-list
    # vs numbered-text fallback is the template path's concern, not the plan's.
    long_label = "A very long option label that exceeds twenty-four characters easily"
    options = [{"label": f"Option number {i}"} for i in range(12)]
    schema = default_form_schema_copy()
    schema["pages"].append(
        _generic_page(
            {"type": "single_select", "label": long_label, "options": options},
        )
    )
    plan = build_conversation_plan(schema, "en")
    slot = next(s for s in plan if s.is_generic)
    assert slot.target == long_label
    assert len(slot.options) == 12


def test_locale_resolution_picks_requested_language() -> None:
    schema = default_form_schema_copy()
    schema["pages"].append(
        _generic_page(
            {
                "type": "single_select",
                "label": {"en": "Roof intact?", "ar": "هل السقف سليم؟"},
                "options": [
                    {"label": {"en": "Yes", "ar": "نعم"}},
                    {"label": {"en": "No", "ar": "لا"}},
                ],
            },
        )
    )
    plan_ar = build_conversation_plan(schema, "ar")
    slot = next(s for s in plan_ar if s.is_generic)
    assert slot.target == "هل السقف سليم؟"
    assert slot.options == ("نعم", "لا")


def test_malformed_pages_are_skipped() -> None:
    schema: dict[str, Any] = {
        "pages": [
            {"kind": "photo_and_damage", "enabled": True, "locked": True},
            "not-a-dict",
            {"enabled": True},  # no kind
            {"kind": "unknown_future_kind", "enabled": True},
            {"kind": "debris", "enabled": True, "locked": False},
        ]
    }
    plan = build_conversation_plan(schema, "en")
    assert _targets(plan) == ["debris"]


def test_empty_or_missing_pages_returns_empty() -> None:
    assert build_conversation_plan({}, "en") == []
    assert build_conversation_plan({"pages": "nope"}, "en") == []


# ---- missing_fields (the submit gate) -----------------------------------


def _full_builtin_session(schema: dict[str, Any]) -> Session:
    """A session with every always-required field + enabled built-ins set."""
    s = Session(phone_e164="+9665")
    s.crisis_id = uuid.uuid4()
    s.photo_bytes = b"x"
    s.damage_class = "partial"
    s.debris = "no"
    s.infra_type = ["residential"]
    s.crisis_nature = "Earthquake"
    s.location = (33.5, 36.3)
    s.form_schema = schema
    return s


def test_missing_fields_empty_when_default_complete() -> None:
    schema = default_form_schema_copy()
    s = _full_builtin_session(schema)
    plan = build_conversation_plan(schema, "en")
    assert missing_fields(s, plan) == []


def test_missing_fields_lists_unset_builtins() -> None:
    schema = default_form_schema_copy()
    s = _full_builtin_session(schema)
    s.debris = None
    s.location = None
    plan = build_conversation_plan(schema, "en")
    assert missing_fields(s, plan) == ["debris", "location"]


def test_disabled_builtin_not_required() -> None:
    schema = default_form_schema_copy()
    for page in schema["pages"]:
        if page["kind"] == "debris":
            page["enabled"] = False
    s = _full_builtin_session(schema)
    s.debris = None  # disabled → must not block
    plan = build_conversation_plan(schema, "en")
    assert missing_fields(s, plan) == []


def test_optional_builtin_does_not_block_submission() -> None:
    # An enabled-but-optional built-in select (required=false) is asked but must
    # not block submission when left unset.
    schema = default_form_schema_copy()
    for page in schema["pages"]:
        if page["kind"] == "debris":
            page["required"] = False
    s = _full_builtin_session(schema)
    s.debris = None  # optional → must not block
    plan = build_conversation_plan(schema, "en")
    assert missing_fields(s, plan) == []
    # Sibling selects stay required.
    s.infra_type = []
    assert missing_fields(s, plan) == ["infra_type"]


def test_required_generic_blocks_until_answered() -> None:
    schema = default_form_schema_copy()
    schema["pages"].append(
        _generic_page(
            {"type": "free_text", "label": "Household size?", "required": True},
            {"type": "free_text", "label": "Notes?", "required": False},
        )
    )
    s = _full_builtin_session(schema)
    plan = build_conversation_plan(schema, "en")
    # Required generic unanswered → listed; optional one never blocks.
    assert missing_fields(s, plan) == ["Household size?"]

    s.generic_answers["Household size?"] = "5"
    assert missing_fields(s, plan) == []

    # An empty/whitespace answer does not count.
    s.generic_answers["Household size?"] = "   "
    assert missing_fields(s, plan) == ["Household size?"]


def test_photo_not_required_when_description_present() -> None:
    # "photo OR description": a description satisfies the gate's photo half when
    # the description page is enabled (default schema enables it).
    schema = default_form_schema_copy()
    s = _full_builtin_session(schema)
    s.photo_bytes = None
    s.photo_media_url = None
    s.infra_description = "Collapsed north wall."
    plan = build_conversation_plan(schema, "en")
    assert missing_fields(s, plan) == []


def test_photo_required_when_no_description() -> None:
    schema = default_form_schema_copy()
    s = _full_builtin_session(schema)
    s.photo_bytes = None
    s.photo_media_url = None
    s.infra_description = None
    plan = build_conversation_plan(schema, "en")
    assert missing_fields(s, plan) == ["photo"]


def test_photo_required_when_description_page_disabled_even_if_text_set() -> None:
    # If the description page is disabled, the submitter won't forward the text,
    # so it can't satisfy the gate — photo stays required.
    schema = default_form_schema_copy()
    for page in schema["pages"]:
        if page["kind"] == "description":
            page["enabled"] = False
    s = _full_builtin_session(schema)
    s.photo_bytes = None
    s.photo_media_url = None
    s.infra_description = "set but page off"
    plan = build_conversation_plan(schema, "en")
    assert "photo" in missing_fields(s, plan)


def test_whitespace_description_does_not_satisfy_photo() -> None:
    schema = default_form_schema_copy()
    s = _full_builtin_session(schema)
    s.photo_bytes = None
    s.photo_media_url = None
    s.infra_description = "   "
    plan = build_conversation_plan(schema, "en")
    assert missing_fields(s, plan) == ["photo"]


def test_route_description_satisfies_location() -> None:
    schema = default_form_schema_copy()
    s = _full_builtin_session(schema)
    s.location = None
    s.route_description = "Behind the old market, blue gate, second street."
    plan = build_conversation_plan(schema, "en")
    assert missing_fields(s, plan) == []


def test_location_required_when_no_pin_and_no_route() -> None:
    schema = default_form_schema_copy()
    s = _full_builtin_session(schema)
    s.location = None
    s.route_description = None
    plan = build_conversation_plan(schema, "en")
    assert missing_fields(s, plan) == ["location"]


def test_whitespace_route_does_not_satisfy_location() -> None:
    schema = default_form_schema_copy()
    s = _full_builtin_session(schema)
    s.location = None
    s.route_description = "   "
    plan = build_conversation_plan(schema, "en")
    assert missing_fields(s, plan) == ["location"]
