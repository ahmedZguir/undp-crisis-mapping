"""Publish-time translation diff for form labels.

Matching is positional since the schema has no ids, so a reorder re-translates
everything after the first moved item.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

TranslateFn = Callable[[str], dict[str, str]]


_LABEL_KEYS: tuple[str, ...] = ("title", "label", "placeholder")
# (page_idx, "page", key), (page_idx, q_idx, "question", key) or
# (page_idx, q_idx, o_idx, "option", key).
PathKey = tuple[Any, ...]


def _prior_index(prior_schema: dict[str, Any]) -> dict[PathKey, Any]:
    index: dict[PathKey, Any] = {}
    pages = prior_schema.get("pages") if isinstance(prior_schema, dict) else None
    if not isinstance(pages, list):
        return index
    for pi, page in enumerate(pages):
        if not isinstance(page, dict):
            continue
        for k in _LABEL_KEYS:
            if k in page:
                index[(pi, "page", k)] = page[k]
        for qi, q in enumerate(page.get("questions") or []):
            if not isinstance(q, dict):
                continue
            for k in _LABEL_KEYS:
                if k in q:
                    index[(pi, qi, "question", k)] = q[k]
            for oi, opt in enumerate(q.get("options") or []):
                if not isinstance(opt, dict):
                    continue
                for k in _LABEL_KEYS:
                    if k in opt:
                        index[(pi, qi, oi, "option", k)] = opt[k]
    return index


def _matches_prior(prior_value: Any, source: str) -> bool:
    """Match on any locale, since the admin may have typed the source in any of them."""
    if isinstance(prior_value, dict):
        return any(v == source for v in prior_value.values())
    return prior_value == source


def merge_translations(
    new_schema: dict[str, Any],
    prior_schema: dict[str, Any],
    translate_fn: TranslateFn,
) -> dict[str, Any]:
    """Replace each source-string label with a locale map.

    Reuses the prior map when the source is unchanged at the same position,
    otherwise calls `translate_fn`. Labels that are already dicts pass through.
    """
    prior = _prior_index(prior_schema)
    pages_in = new_schema.get("pages")
    if not isinstance(pages_in, list):
        return new_schema

    out_pages: list[dict[str, Any]] = []
    for pi, page in enumerate(pages_in):
        if not isinstance(page, dict):
            out_pages.append(page)
            continue
        out_page = dict(page)
        for k in _LABEL_KEYS:
            if k in out_page:
                out_page[k] = _resolve_label(out_page[k], prior.get((pi, "page", k)), translate_fn)
        questions = out_page.get("questions")
        if isinstance(questions, list):
            out_questions: list[dict[str, Any]] = []
            for qi, q in enumerate(questions):
                if not isinstance(q, dict):
                    out_questions.append(q)
                    continue
                out_q = dict(q)
                for k in _LABEL_KEYS:
                    if k in out_q:
                        out_q[k] = _resolve_label(
                            out_q[k], prior.get((pi, qi, "question", k)), translate_fn
                        )
                options = out_q.get("options")
                if isinstance(options, list):
                    out_opts: list[dict[str, Any]] = []
                    for oi, opt in enumerate(options):
                        if not isinstance(opt, dict):
                            out_opts.append(opt)
                            continue
                        out_opt = dict(opt)
                        for k in _LABEL_KEYS:
                            if k in out_opt:
                                out_opt[k] = _resolve_label(
                                    out_opt[k],
                                    prior.get((pi, qi, oi, "option", k)),
                                    translate_fn,
                                )
                        out_opts.append(out_opt)
                    out_q["options"] = out_opts
                out_questions.append(out_q)
            out_page["questions"] = out_questions
        out_pages.append(out_page)

    return {**new_schema, "pages": out_pages}


def _resolve_label(
    incoming: Any,
    prior_value: Any,
    translate_fn: TranslateFn,
) -> Any:
    if isinstance(incoming, dict):
        return incoming
    if not isinstance(incoming, str):
        return incoming
    if prior_value is not None and _matches_prior(prior_value, incoming):
        return prior_value
    return translate_fn(incoming)
