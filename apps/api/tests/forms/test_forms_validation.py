"""Unit tests for `api.forms.validation.validate_form_schema`.

Schema identity is positional (no ids). Label uniqueness within a page
(questions) and within a question (options) becomes a hard requirement —
those checks are exercised here.
"""

from __future__ import annotations

from api.crises.default_form import default_form_schema_copy
from api.forms.validation import validate_form_schema


def test_default_schema_validates_clean() -> None:
    assert validate_form_schema(default_form_schema_copy()) == []


def test_missing_pages_array_returns_single_error() -> None:
    errors = validate_form_schema({})
    assert errors == [{"path": "$.pages", "message": "pages must be a non-empty array"}]


def test_photo_must_be_at_position_zero_and_locked() -> None:
    s = default_form_schema_copy()
    s["pages"][0] = {"kind": "description", "enabled": True, "locked": True}
    errors = validate_form_schema(s)
    assert any(e["path"] == "$.pages[0].kind" for e in errors)


def test_location_must_be_at_position_one_and_locked() -> None:
    s = default_form_schema_copy()
    s["pages"][1] = {"kind": "description", "enabled": True, "locked": False}
    errors = validate_form_schema(s)
    assert any(e["path"] == "$.pages[1].kind" for e in errors)
    assert any(e["path"] == "$.pages[1].locked" for e in errors)


def test_other_pages_cannot_be_locked() -> None:
    s = default_form_schema_copy()
    s["pages"][3]["locked"] = True
    errors = validate_form_schema(s)
    assert any(e["path"] == "$.pages[3].locked" for e in errors)


def test_unknown_kind_is_rejected() -> None:
    s = default_form_schema_copy()
    s["pages"].append({"kind": "novel_kind", "enabled": True, "locked": False})
    errors = validate_form_schema(s)
    assert any("unknown kind" in e["message"] for e in errors)


def test_generic_page_requires_at_least_one_question() -> None:
    s = default_form_schema_copy()
    s["pages"].append(
        {
            "kind": "generic",
            "enabled": True,
            "locked": False,
            "title": "Empty",
            "questions": [],
        }
    )
    errors = validate_form_schema(s)
    assert any(e["message"].endswith("≥1 question") for e in errors)


def test_select_question_requires_options() -> None:
    s = default_form_schema_copy()
    s["pages"].append(
        {
            "kind": "generic",
            "enabled": True,
            "locked": False,
            "title": "Q",
            "questions": [
                {
                    "type": "single_select",
                    "label": "Pick",
                    "options": [],
                }
            ],
        }
    )
    errors = validate_form_schema(s)
    assert any("≥1 option" in e["message"] for e in errors)


def test_max_select_must_be_within_bounds() -> None:
    s = default_form_schema_copy()
    s["pages"].append(
        {
            "kind": "generic",
            "enabled": True,
            "locked": False,
            "title": "Q",
            "questions": [
                {
                    "type": "multi_select",
                    "label": "Pick many",
                    "max_select": 99,
                    "options": [{"label": "A"}, {"label": "B"}],
                }
            ],
        }
    )
    errors = validate_form_schema(s)
    assert any("max_select" in e["path"] for e in errors)


def test_duplicate_question_labels_are_rejected_within_a_page() -> None:
    s = default_form_schema_copy()
    s["pages"].append(
        {
            "kind": "generic",
            "enabled": True,
            "locked": False,
            "title": "Q",
            "questions": [
                {"type": "free_text", "label": "Tell us"},
                {"type": "free_text", "label": "Tell us"},
            ],
        }
    )
    errors = validate_form_schema(s)
    assert any("duplicate question label" in e["message"] for e in errors)


def test_duplicate_option_labels_are_rejected_within_a_question() -> None:
    s = default_form_schema_copy()
    s["pages"].append(
        {
            "kind": "generic",
            "enabled": True,
            "locked": False,
            "title": "Q",
            "questions": [
                {
                    "type": "single_select",
                    "label": "Pick",
                    "options": [{"label": "Yes"}, {"label": "Yes"}],
                }
            ],
        }
    )
    errors = validate_form_schema(s)
    assert any("duplicate option label" in e["message"] for e in errors)


def test_empty_question_label_is_rejected() -> None:
    s = default_form_schema_copy()
    s["pages"].append(
        {
            "kind": "generic",
            "enabled": True,
            "locked": False,
            "title": "Q",
            "questions": [{"type": "free_text", "label": "   "}],
        }
    )
    errors = validate_form_schema(s)
    assert any(e["message"] == "label is required" for e in errors)
