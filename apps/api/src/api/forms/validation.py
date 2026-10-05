"""Form-schema validation returning `[{path, message}, ...]`; empty means valid.

The schema has no ids. Answers are keyed by question label and store option
labels, so labels must be unique within a page and within a question.
"""

from __future__ import annotations

from typing import Any

KNOWN_KINDS: frozenset[str] = frozenset(
    {
        "photo_and_damage",
        "location",
        "description",
        "debris",
        "infra_type",
        "crisis_nature",
        "electricity",
        "health_services",
        "pressing_needs",
        "generic",
    }
)


def _err(path: str, message: str) -> dict[str, str]:
    return {"path": path, "message": message}


def validate_form_schema(schema: Any) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []

    if not isinstance(schema, dict):
        return [_err("$", "schema must be an object")]

    pages = schema.get("pages")
    if not isinstance(pages, list) or len(pages) == 0:
        return [_err("$.pages", "pages must be a non-empty array")]

    def _check_locked(pos: int, kind: str) -> None:
        if pos >= len(pages):
            errors.append(_err(f"$.pages[{pos}]", f"missing required page '{kind}'"))
            return
        page = pages[pos]
        if not isinstance(page, dict):
            errors.append(_err(f"$.pages[{pos}]", "page must be an object"))
            return
        if page.get("kind") != kind:
            errors.append(_err(f"$.pages[{pos}].kind", f"must be '{kind}' at position {pos}"))
        if page.get("enabled") is not True:
            errors.append(_err(f"$.pages[{pos}].enabled", f"'{kind}' must be enabled"))
        if page.get("locked") is not True:
            errors.append(_err(f"$.pages[{pos}].locked", f"'{kind}' must be locked"))

    _check_locked(0, "photo_and_damage")
    _check_locked(1, "location")

    for i, page in enumerate(pages):
        if not isinstance(page, dict):
            errors.append(_err(f"$.pages[{i}]", "page must be an object"))
            continue

        kind = page.get("kind")
        if kind not in KNOWN_KINDS:
            errors.append(_err(f"$.pages[{i}].kind", f"unknown kind '{kind}'"))

        if i >= 2 and page.get("locked") is True:
            errors.append(
                _err(
                    f"$.pages[{i}].locked",
                    "only photo_and_damage/location may be locked",
                )
            )

        # Optional; absent means required for selects.
        if "required" in page and not isinstance(page.get("required"), bool):
            errors.append(_err(f"$.pages[{i}].required", "required must be a boolean"))

        if kind == "generic":
            _validate_generic_page(page, i, errors)

    return errors


def _validate_generic_page(
    page: dict[str, Any],
    i: int,
    errors: list[dict[str, str]],
) -> None:
    questions = page.get("questions")
    if not isinstance(questions, list) or len(questions) == 0:
        errors.append(_err(f"$.pages[{i}].questions", "generic page must have ≥1 question"))
        return
    # generic_answers is keyed by label, so duplicates would share one answer slot.
    seen_question_labels: set[str] = set()
    for j, q in enumerate(questions):
        qpath = f"$.pages[{i}].questions[{j}]"
        if not isinstance(q, dict):
            errors.append(_err(qpath, "question must be an object"))
            continue

        label = q.get("label")
        if not isinstance(label, str) or not label.strip():
            errors.append(_err(f"{qpath}.label", "label is required"))
        elif label in seen_question_labels:
            errors.append(_err(f"{qpath}.label", f"duplicate question label '{label}'"))
        else:
            seen_question_labels.add(label)

        qtype = q.get("type")
        if qtype not in ("single_select", "multi_select", "free_text"):
            errors.append(_err(f"{qpath}.type", f"unknown type '{qtype}'"))

        if qtype in ("single_select", "multi_select"):
            _validate_options(q, qpath, errors)
        if qtype == "multi_select":
            _validate_max_select(q, qpath, errors)


def _validate_options(q: dict[str, Any], qpath: str, errors: list[dict[str, str]]) -> None:
    options = q.get("options")
    if not isinstance(options, list) or len(options) == 0:
        errors.append(_err(f"{qpath}.options", "select question must have ≥1 option"))
        return
    seen_option_labels: set[str] = set()
    for k, opt in enumerate(options):
        opath = f"{qpath}.options[{k}]"
        if not isinstance(opt, dict):
            errors.append(_err(opath, "option must be an object"))
            continue
        label = opt.get("label")
        if not isinstance(label, str) or not label.strip():
            errors.append(_err(f"{opath}.label", "option label is required"))
            continue
        # Stored answers are the labels themselves.
        if label in seen_option_labels:
            errors.append(_err(f"{opath}.label", f"duplicate option label '{label}'"))
        else:
            seen_option_labels.add(label)


def _validate_max_select(q: dict[str, Any], qpath: str, errors: list[dict[str, str]]) -> None:
    max_select = q.get("max_select")
    if max_select is None:
        return
    options = q.get("options") or []
    if (
        not isinstance(max_select, int)
        or isinstance(max_select, bool)
        or max_select < 1
        or max_select > len(options)
    ):
        errors.append(_err(f"{qpath}.max_select", "max_select must be int in [1, options_count]"))
