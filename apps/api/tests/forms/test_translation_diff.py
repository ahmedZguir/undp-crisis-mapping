"""Unit tests for `merge_translations`.

Schema identity is positional, so matches are by index across `pages`,
`questions`, and `options`. Reorders break matches by design; the tests
exercise the same-position-same-source-string fast path and the
changed-string slow path.
"""

from __future__ import annotations

from api.forms.translation_diff import merge_translations


def _generic_page(title, questions):
    return {
        "kind": "generic",
        "enabled": True,
        "locked": False,
        "title": title,
        "questions": questions,
    }


def test_unchanged_labels_at_same_positions_reuse_prior_maps() -> None:
    def translate_fn(_source: str) -> dict[str, str]:
        raise AssertionError("translate_fn should not be called for unchanged labels")

    prior = {
        "pages": [
            {"kind": "photo_and_damage", "enabled": True, "locked": True},
            {"kind": "location", "enabled": True, "locked": True},
            _generic_page(
                {"en": "Pressing needs", "ar": "احتياجات"},
                [
                    {
                        "type": "free_text",
                        "label": {"en": "Tell us", "ar": "أخبرنا"},
                    }
                ],
            ),
        ]
    }
    incoming = {
        "pages": [
            {"kind": "photo_and_damage", "enabled": True, "locked": True},
            {"kind": "location", "enabled": True, "locked": True},
            _generic_page(
                "Pressing needs",
                [{"type": "free_text", "label": "Tell us"}],
            ),
        ]
    }

    out = merge_translations(incoming, prior, translate_fn)
    assert out["pages"][2]["title"] == prior["pages"][2]["title"]
    assert out["pages"][2]["questions"][0]["label"] == prior["pages"][2]["questions"][0]["label"]


def test_changed_label_triggers_translate_exactly_once() -> None:
    calls: list[str] = []

    def translate_fn(source: str) -> dict[str, str]:
        calls.append(source)
        return {"en": source, "ar": f"AR:{source}"}

    prior = {
        "pages": [
            {"kind": "photo_and_damage", "enabled": True, "locked": True},
            {"kind": "location", "enabled": True, "locked": True},
            _generic_page({"en": "Old title", "ar": "قديم"}, []),
        ]
    }
    incoming = {
        "pages": [
            {"kind": "photo_and_damage", "enabled": True, "locked": True},
            {"kind": "location", "enabled": True, "locked": True},
            _generic_page("New title", []),
        ]
    }

    out = merge_translations(incoming, prior, translate_fn)
    assert calls == ["New title"]
    assert out["pages"][2]["title"] == {"en": "New title", "ar": "AR:New title"}


def test_already_locale_map_input_passes_through() -> None:
    def translate_fn(_source: str) -> dict[str, str]:
        raise AssertionError("not called when input is already a map")

    incoming = {
        "pages": [
            {"kind": "photo_and_damage", "enabled": True, "locked": True},
            {"kind": "location", "enabled": True, "locked": True},
            _generic_page({"en": "X", "ar": "Y"}, []),
        ]
    }
    out = merge_translations(incoming, {}, translate_fn)
    assert out["pages"][2]["title"] == {"en": "X", "ar": "Y"}


def test_new_page_with_no_prior_triggers_translate() -> None:
    calls: list[str] = []

    def translate_fn(source: str) -> dict[str, str]:
        calls.append(source)
        return {"en": source}

    incoming = {
        "pages": [
            {"kind": "photo_and_damage", "enabled": True, "locked": True},
            {"kind": "location", "enabled": True, "locked": True},
            _generic_page("Fresh page", []),
        ]
    }
    merge_translations(incoming, {}, translate_fn)
    assert calls == ["Fresh page"]


def test_options_match_by_position_within_question() -> None:
    """Option at position 0 with the same source string reuses the prior
    map; option at position 1 with a different source triggers a call."""
    calls: list[str] = []

    def translate_fn(source: str) -> dict[str, str]:
        calls.append(source)
        return {"en": source, "ar": f"AR:{source}"}

    prior = {
        "pages": [
            {"kind": "photo_and_damage", "enabled": True, "locked": True},
            {"kind": "location", "enabled": True, "locked": True},
            _generic_page(
                {"en": "Pick", "ar": "اختر"},
                [
                    {
                        "type": "single_select",
                        "label": {"en": "Pick", "ar": "اختر"},
                        "options": [
                            {"label": {"en": "Yes", "ar": "نعم"}},
                            {"label": {"en": "Maybe", "ar": "ربما"}},
                        ],
                    }
                ],
            ),
        ]
    }
    incoming = {
        "pages": [
            {"kind": "photo_and_damage", "enabled": True, "locked": True},
            {"kind": "location", "enabled": True, "locked": True},
            _generic_page(
                "Pick",
                [
                    {
                        "type": "single_select",
                        "label": "Pick",
                        "options": [
                            {"label": "Yes"},
                            {"label": "Definitely"},
                        ],
                    }
                ],
            ),
        ]
    }
    out = merge_translations(incoming, prior, translate_fn)
    assert calls == ["Definitely"]
    opts = out["pages"][2]["questions"][0]["options"]
    assert opts[0]["label"] == {"en": "Yes", "ar": "نعم"}
    assert opts[1]["label"] == {"en": "Definitely", "ar": "AR:Definitely"}
