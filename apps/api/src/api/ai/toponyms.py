"""Extract place mentions from a citizen's free-text location for geocoding.

A missed place is recoverable but an invented one misdirects responders, so
the prompt biases toward omission. JSON mode keeps the reasoning model from
emitting a prose preamble, and it requires an object, hence `{"toponyms": [...]}`.
"""

from __future__ import annotations

from typing import Any, cast, get_args

from api.ai import prompts
from api.ai.client import AIClients
from api.ai.llm import LLMOutputError, complete_text, parse_json
from api.ai.types import ToponymMention, ToponymType

_ALLOWED_TYPES: tuple[str, ...] = get_args(ToponymType)


# Caps adversarial input; still room for many mentions.
_MAX_OUTPUT_TOKENS = 1024


async def extract_toponyms(text: str, *, clients: AIClients) -> list[ToponymMention]:
    """Return mentions in text order; an empty list is a common, valid result.

    Raises `LLMOutputError` on malformed output, which the worker treats as terminal.
    """
    cfg = clients.require_text()
    content = await complete_text(
        cfg,
        prompts.TOPONYMS_SYSTEM,
        text,
        max_tokens=_MAX_OUTPUT_TOKENS,
        temperature=0.0,
        json_mode=True,
    )
    return _parse_response(content)


def _parse_response(raw: str) -> list[ToponymMention]:
    data = parse_json(raw)
    items = _toponym_list(data)
    mentions: list[ToponymMention] = []
    for item in items:
        mention = _parse_one(item)
        if mention is not None:
            mentions.append(mention)
    return mentions


def _toponym_list(data: Any) -> list[Any]:
    """Accept the wrapper variants the model actually emits.

    Handles `{"toponyms": [...]}`, `{"toponyms": {...}}`, a bare array, a bare
    mention object, and `{}`. Any other non-empty object is an error.
    """
    if isinstance(data, list):
        return cast(list[Any], data)
    if isinstance(data, dict):
        obj = cast(dict[str, Any], data)
        toponyms = obj.get("toponyms")
        if isinstance(toponyms, list):
            return cast(list[Any], toponyms)
        if isinstance(toponyms, dict):
            return [toponyms]
        if "surface_form" in obj:
            return [obj]
        if not obj:
            return []
        raise LLMOutputError("model output object has no `toponyms` array")
    raise LLMOutputError("model output is not a JSON object or array")


def _parse_one(item: Any) -> ToponymMention | None:
    """Return None for a malformed element so one bad entry doesn't sink the rest."""
    if not isinstance(item, dict):
        return None
    obj = cast(dict[str, Any], item)
    surface = obj.get("surface_form")
    if not isinstance(surface, str) or not surface.strip():
        return None

    type_hint = obj.get("type_hint")
    if not isinstance(type_hint, str) or type_hint not in _ALLOWED_TYPES:
        # The hint is only a soft signal, so fall back to the coarsest bucket.
        type_hint = "area"

    corrected = obj.get("corrected_form")
    corrected_form = corrected if isinstance(corrected, str) and corrected.strip() else None

    return ToponymMention(
        surface_form=surface,
        type_hint=cast(ToponymType, type_hint),
        corrected_form=corrected_form,
    )
