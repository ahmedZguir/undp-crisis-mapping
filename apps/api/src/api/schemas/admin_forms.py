"""Admin form-schema models.

The wire field is "schema", but a Python attribute of that name shadows
BaseModel.schema(), so the attribute is form_schema with an alias.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_MODEL_CONFIG = ConfigDict(populate_by_name=True)


class FormPublishPayload(BaseModel):
    model_config = _MODEL_CONFIG
    based_on_version: int
    form_schema: dict[str, Any] = Field(alias="schema")


class FormPublishResponse(BaseModel):
    model_config = _MODEL_CONFIG
    version: int
    form_schema: dict[str, Any] = Field(alias="schema", serialization_alias="schema")


class FormGetResponse(BaseModel):
    model_config = _MODEL_CONFIG
    version: int
    form_schema: dict[str, Any] = Field(alias="schema", serialization_alias="schema")
