"""Language detection and translation to English in one JSON-mode LLM call."""

from __future__ import annotations

import json
import logging
from typing import Any, cast

from api.ai import prompts
from api.ai.client import AIClients
from api.ai.llm import LLMOutputError, complete_json, complete_text, parse_json
from api.ai.types import TranslationResult

_logger = logging.getLogger(__name__)


# Bounds cost on adversarial input that tries to balloon the reply.
_MAX_OUTPUT_TOKENS = 1024


async def detect_and_translate(text: str, *, clients: AIClients) -> TranslationResult:
    """Raises `LLMOutputError` on malformed output, which the worker treats as terminal.

    Transport errors propagate so Arq can retry.
    """
    cfg = clients.require_text()
    content = await complete_text(
        cfg,
        prompts.TRANSLATION_SYSTEM,
        text,
        max_tokens=_MAX_OUTPUT_TOKENS,
        temperature=0.0,
        json_mode=True,
    )
    return _parse_response(content)


async def translate_to_all_locales(
    text: str,
    locales: list[str],
    *,
    clients: AIClients,
) -> dict[str, str]:
    """Translate `text` into every locale in one call. Never raises.

    Every requested locale is a key in the result; any locale that fails or is
    missing gets the source text, so a publish never blocks on translation.
    """
    fallback = {locale: text for locale in locales}
    try:
        cfg = clients.require_text()
    except Exception:
        _logger.warning("translate_to_all_locales: AI client unavailable; using fallback")
        return fallback

    system = (
        "You translate a short source string into every requested locale.\n"
        "Output a JSON object whose keys are the requested locale codes\n"
        f"({', '.join(locales)}) and whose values are the translation in\n"
        "that locale. Preserve meaning, not surface form. Output JSON only.\n"
        "If a locale matches the source language, return the source verbatim."
    )
    user = json.dumps({"text": text, "locales": locales})

    try:
        data = await complete_json(
            cfg, system, user, max_tokens=_MAX_OUTPUT_TOKENS, temperature=0.0
        )
    except Exception:
        _logger.warning("translate_to_all_locales: LLM call failed; using fallback", exc_info=True)
        return fallback

    if not isinstance(data, dict):
        return fallback

    obj = cast(dict[str, Any], data)
    out: dict[str, str] = {}
    for locale in locales:
        value = obj.get(locale)
        out[locale] = value if isinstance(value, str) and value else text
    return out


def _parse_response(raw: str) -> TranslationResult:
    data = parse_json(raw)
    if not isinstance(data, dict):
        raise LLMOutputError("model output is not a JSON object")
    obj = cast(dict[str, Any], data)
    lang = obj.get("lang")
    text_en = obj.get("text_en", "")
    if not isinstance(lang, str) or not lang:
        raise LLMOutputError("model output missing or empty `lang`")
    if not isinstance(text_en, str):
        raise LLMOutputError("model output `text_en` is not a string")
    return TranslationResult(lang=lang, text_en=text_en)
