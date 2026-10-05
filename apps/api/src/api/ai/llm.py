"""Chat-completion helpers shared by every text-model caller.

Every call disables Qwen "thinking": served thinking models otherwise prepend a
chain-of-thought that leaks into the answer and, in JSON mode, returns
well-formed but wrong shapes. Templates that ignore the flag are unaffected.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from openai import omit

from api.ai.client import ConfiguredClient

logger = logging.getLogger(__name__)

NO_THINK_EXTRA_BODY = {"chat_template_kwargs": {"enable_thinking": False}}


class LLMOutputError(ValueError):
    """The model returned no choices or output that doesn't match the expected shape."""


async def complete_chat(
    cfg: ConfiguredClient,
    messages: list[dict[str, Any]],
    *,
    max_tokens: int,
    temperature: float,
    json_mode: bool = False,
) -> str:
    """Return the stripped reply text. Transport errors propagate."""
    response = await cfg.client.chat.completions.create(
        model=cfg.model,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=messages,  # pyright: ignore[reportArgumentType]
        response_format={"type": "json_object"} if json_mode else omit,
        extra_body=NO_THINK_EXTRA_BODY,
    )
    choices = response.choices
    if not choices:
        raise LLMOutputError("model returned no choices")
    return (choices[0].message.content or "").strip()


async def complete_text(
    cfg: ConfiguredClient,
    system: str,
    user: str,
    *,
    max_tokens: int,
    temperature: float,
    json_mode: bool = False,
) -> str:
    return await complete_chat(
        cfg,
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=max_tokens,
        temperature=temperature,
        json_mode=json_mode,
    )


async def complete_json(
    cfg: ConfiguredClient,
    system: str,
    user: str,
    *,
    max_tokens: int,
    temperature: float,
) -> Any:
    """JSON-mode call; returns the parsed value or raises `LLMOutputError`."""
    raw = await complete_text(
        cfg, system, user, max_tokens=max_tokens, temperature=temperature, json_mode=True
    )
    return parse_json(raw)


def parse_json(raw: str) -> Any:
    """Parse the outermost JSON value, tolerating code fences and a prose lead-in."""
    try:
        return json.loads(_extract_json(raw))
    except json.JSONDecodeError as exc:
        logger.warning("ai.llm.invalid_json raw=%r", raw[:200])
        raise LLMOutputError(f"model output is not JSON: {exc}") from exc


def _extract_json(raw: str) -> str:
    """Return the span from the earliest bracket to its matching closer kind.

    Returns the input unchanged when no bracket is found so `json.loads` reports the error.
    """
    obj_start, arr_start = raw.find("{"), raw.find("[")
    openers = [i for i in (obj_start, arr_start) if i != -1]
    if not openers:
        return raw
    start = min(openers)
    end = raw.rfind("}") if raw[start] == "{" else raw.rfind("]")
    if end > start:
        return raw[start : end + 1]
    return raw


__all__ = [
    "NO_THINK_EXTRA_BODY",
    "LLMOutputError",
    "complete_chat",
    "complete_json",
    "complete_text",
    "parse_json",
]
