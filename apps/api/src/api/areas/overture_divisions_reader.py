"""Admin-area lookups over Overture's divisions theme."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Area:
    """Typeahead row. parents are ancestor names for disambiguation; bbox is
    (xmin, ymin, xmax, ymax)."""

    id: str
    name: str
    subtype: str
    country: str | None
    parents: list[str]
    bbox: tuple[float, float, float, float]


@dataclass(frozen=True)
class ResolvedArea:
    wkb: bytes
    country: str | None


class AreaNotFoundError(ValueError):
    """Unknown GERS division id."""


@dataclass(frozen=True)
class Country:
    """iso2 is uppercase ISO 3166-1 alpha-2, as stored in crises.countries."""

    id: str
    iso2: str
    name: str


class OvertureDivisionsReader(Protocol):
    async def search(self, q: str, country: str | None, limit: int) -> list[Area]: ...

    async def list_countries(self) -> list[Country]: ...

    async def read_area_geometry(self, area_id: str) -> ResolvedArea: ...

    async def read_area_geojson(self, area_id: str) -> dict[str, object]: ...

    async def read_countries_union(self, iso2_codes: list[str]) -> bytes | None: ...

    async def read_countries_geojson_union(
        self, iso2_codes: list[str]
    ) -> dict[str, object] | None: ...


def as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]


def as_float(value: object) -> float:
    if isinstance(value, int | float):
        return float(value)
    raise TypeError(f"expected numeric bbox component, got {type(value).__name__}")


__all__ = [
    "Area",
    "AreaNotFoundError",
    "Country",
    "OvertureDivisionsReader",
    "ResolvedArea",
]
