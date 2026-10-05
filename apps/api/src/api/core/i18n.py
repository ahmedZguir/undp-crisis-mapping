"""Supported locales and label resolution. Keep in sync with apps/pwa/src/i18n.ts."""

from __future__ import annotations

import contextlib
from typing import Any

SUPPORTED_LOCALES: tuple[str, ...] = ("en", "ar", "fr", "es", "zh", "ru")
DEFAULT_LOCALE: str = "en"


def parse_accept_language(header: str) -> list[str]:
    """Language subtags ('ar-EG' becomes 'ar') ordered by q value; malformed entries are dropped."""
    result: list[tuple[float, str]] = []
    for i, raw in enumerate(header.split(",")):
        item = raw.strip()
        if not item:
            continue
        if ";" in item:
            tag, _, rest = item.partition(";")
            tag = tag.strip().lower()
            q = 1.0
            for part in rest.split(";"):
                part = part.strip()
                if part.startswith("q="):
                    with contextlib.suppress(ValueError):
                        q = float(part[2:])
        else:
            tag = item.lower()
            q = 1.0
        if not tag:
            continue
        lang = tag.split("-", 1)[0]
        # Negative index preserves header order for ties.
        result.append((-q, -i, lang))  # pyright: ignore[reportArgumentType]
    result.sort()
    seen: set[str] = set()
    out: list[str] = []
    for entry in result:
        lang = entry[2] if len(entry) == 3 else None
        if lang is None or lang in seen:
            continue
        seen.add(lang)
        out.append(lang)
    return out


def pick_locale(*, query: str | None, accept_language: str | None) -> str:
    """Pick ?locale=, then Accept-Language, then the default, skipping unsupported values."""
    if query:
        candidate = query.lower().split("-", 1)[0]
        if candidate in SUPPORTED_LOCALES:
            return candidate
    if accept_language:
        for lang in parse_accept_language(accept_language):
            if lang in SUPPORTED_LOCALES:
                return lang
    return DEFAULT_LOCALE


def resolve_label(label: Any, locale: str, fallback: str = DEFAULT_LOCALE) -> Any:
    """Strings pass through; locale maps try `locale`, then `fallback`, then any value."""
    if isinstance(label, str):
        return label
    if isinstance(label, dict):
        if locale in label:
            return label[locale]
        if fallback in label:
            return label[fallback]
        for v in label.values():
            return v
        return ""
    return label


_LABEL_KEYS: tuple[str, ...] = ("title", "label", "placeholder")


def resolve_schema_labels(schema: dict[str, Any], locale: str) -> dict[str, Any]:
    """Recursively resolve every label key to a single string."""
    if isinstance(schema, dict):
        out: dict[str, Any] = {}
        for k, v in schema.items():
            if k in _LABEL_KEYS:
                out[k] = resolve_label(v, locale)
            else:
                out[k] = resolve_schema_labels(v, locale)
        return out
    if isinstance(schema, list):
        return [resolve_schema_labels(v, locale) for v in schema]
    return schema
