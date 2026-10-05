"""Language set shared by the text and voice channels."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

# Order drives the numbered language pickers, so this is kept separate from
# api.core.i18n.SUPPORTED_LOCALES, which uses a different order.
Lang = Literal["en", "ar", "es", "fr", "ru", "zh"]
DEFAULT_LANG: Lang = "en"
SUPPORTED_LANGS: tuple[Lang, ...] = ("en", "ar", "es", "fr", "ru", "zh")

# Shown before a language is chosen, so each name is in its own script.
LANGUAGE_NATIVE_NAMES: dict[Lang, str] = {
    "en": "English",
    "ar": "العربية",
    "es": "Español",
    "fr": "Français",
    "ru": "Русский",
    "zh": "中文",
}


def normalize_lang(lang: str | None) -> Lang:
    return lang if lang in SUPPORTED_LANGS else DEFAULT_LANG


def resolve_strings(table: Mapping[str, Mapping[str, str]], lang: str) -> dict[str, str]:
    """English fills any key missing in ``lang``."""
    return {k: v.get(lang, v["en"]) for k, v in table.items()}
