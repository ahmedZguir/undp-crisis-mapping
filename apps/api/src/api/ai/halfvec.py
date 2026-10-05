"""Text literal helpers for pgvector `halfvec` values, shared so float formatting can't drift."""

from __future__ import annotations


def literal(vector: list[float]) -> str:
    """Render `[0.1,0.2,...]`. `float()` also accepts NumPy scalars."""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def parse(text: str) -> list[float]:
    """Parse the `embedding::text` form pgvector emits. Tolerates `[]` and whitespace."""
    body = text.strip()
    if body.startswith("[") and body.endswith("]"):
        body = body[1:-1]
    if not body:
        return []
    return [float(x) for x in body.split(",") if x.strip()]


__all__ = ["literal", "parse"]
