"""Leaf module so primitives can raise this without importing client.py (a cycle)."""

from __future__ import annotations


class AIClientUnavailableError(RuntimeError):
    """Raised when a primitive is called but its client was never configured."""


__all__ = ["AIClientUnavailableError"]
