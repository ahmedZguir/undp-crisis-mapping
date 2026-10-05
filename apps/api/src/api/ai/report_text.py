"""Renders a report as the text the embedder and the LLM prompts see."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def report_detail_lines(
    hit: Mapping[str, Any],
    *,
    caption_format: str = "{}",
    maxlen: int | None = None,
    include_debris: bool = True,
) -> list[str]:
    """Free text (English description, caption), then `Key: value` facts.

    `maxlen` truncates the free text only.
    """
    lines: list[str] = []
    desc = hit.get("description_en") or hit.get("description")
    if desc:
        lines.append(_truncate(desc.strip(), maxlen))
    caption = hit.get("caption")
    if caption:
        lines.append(caption_format.format(_truncate(caption.strip(), maxlen)))
    building = hit.get("building_name") or hit.get("infra_name")
    if building:
        lines.append(f"Building: {building}")
    infra = hit.get("infra_type")
    if infra:
        lines.append(f"Infra: {', '.join(infra)}")
    damage = hit.get("damage_class")
    if damage:
        lines.append(f"Damage: {damage}")
    debris = hit.get("debris")
    if include_debris and debris is not None:
        lines.append(f"Debris: {'yes' if debris else 'no'}")
    return lines


def _truncate(text: str, maxlen: int | None) -> str:
    if maxlen is None or len(text) <= maxlen:
        return text
    return text[: maxlen - 1].rstrip() + "…"
