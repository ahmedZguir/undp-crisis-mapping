"""Default per-crisis form schema.

The migration default is a copy of this JSON; a test keeps them equal. The first
two pages (photo_and_damage, location) are locked in place.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

DEFAULT_FORM_VERSION = 1


DEFAULT_FORM_SCHEMA: dict[str, Any] = {
    "pages": [
        {"kind": "photo_and_damage", "enabled": True, "locked": True},
        {"kind": "location", "enabled": True, "locked": True},
        {"kind": "description", "enabled": True, "locked": False},
        {"kind": "debris", "enabled": True, "locked": False},
        {"kind": "infra_type", "enabled": True, "locked": False},
        {"kind": "crisis_nature", "enabled": True, "locked": False},
        {"kind": "electricity", "enabled": False, "locked": False},
        {"kind": "health_services", "enabled": False, "locked": False},
        {"kind": "pressing_needs", "enabled": False, "locked": False},
    ]
}


def default_form_schema_copy() -> dict[str, Any]:
    """Deep copy, so callers cannot mutate the module constant."""
    return deepcopy(DEFAULT_FORM_SCHEMA)
