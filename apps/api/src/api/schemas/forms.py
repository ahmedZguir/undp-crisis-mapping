"""Public form-schema models. Labels are already resolved to one locale."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class CrisisDetailPublic(BaseModel):
    id: str
    name: str
    status: str
    form_version: int
    form_schema: dict[str, Any]  # locale-resolved
