"""Classifies a photo caption as disaster-relevant or off-topic (selfies, screenshots)."""

from __future__ import annotations

from typing import Any, cast, get_args

from api.ai import prompts
from api.ai.client import AIClients
from api.ai.llm import LLMOutputError, complete_text, parse_json
from api.ai.types import RelevanceLabel, RelevanceResult

_ALLOWED_LABELS: tuple[str, ...] = get_args(RelevanceLabel)


_MAX_OUTPUT_TOKENS = 128


async def score_relevance(
    caption: str,
    *,
    clients: AIClients,
    crisis_context: str | None = None,
) -> RelevanceResult:
    """`crisis_context` (e.g. the crisis name) lets the model weigh the expected disaster type.

    Raises `LLMOutputError` on malformed output, which the worker treats as terminal.
    """
    cfg = clients.require_text()
    user_message = caption
    if crisis_context:
        user_message = f"Crisis: {crisis_context}\n\nCaption: {caption}"
    content = await complete_text(
        cfg,
        prompts.RELEVANCE_SYSTEM,
        user_message,
        max_tokens=_MAX_OUTPUT_TOKENS,
        temperature=0.0,
        json_mode=True,
    )
    return _parse_response(content)


def _parse_response(raw: str) -> RelevanceResult:
    data = parse_json(raw)
    if not isinstance(data, dict):
        raise LLMOutputError("model output is not a JSON object")
    obj = cast(dict[str, Any], data)
    label = obj.get("label")
    score = obj.get("score")
    if not isinstance(label, str) or label not in _ALLOWED_LABELS:
        raise LLMOutputError(f"model output `label` is not one of {_ALLOWED_LABELS!r}: {label!r}")
    if not isinstance(score, int | float):
        raise LLMOutputError("model output `score` is not numeric")
    score_f = float(score)
    if score_f < 0.0 or score_f > 1.0:
        # Small drifts outside [0, 1] are common; clamp them.
        score_f = max(0.0, min(1.0, score_f))
    typed_label = cast("RelevanceLabel", label)
    return RelevanceResult(label=typed_label, score=score_f)
