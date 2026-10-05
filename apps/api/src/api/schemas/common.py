"""Schema primitives shared across feature surfaces."""

from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, Field

DamageClass = Literal["minimal", "partial", "complete"]
Debris = Literal["yes", "no", "unknown"]

# Per-crisis public map exposure. "none" hides public map data but keeps the
# crisis in the citizen picker; "aggregate_view" (default) is the k-anonymous heatmap.
PublicVisibility = Literal["none", "aggregate_view", "buildings", "full"]

CrisisStatus = Literal["inactive", "active", "archived"]


VALID_DAMAGE_CLASSES: frozenset[str] = frozenset(get_args(DamageClass))


def as_damage_class(value: str) -> DamageClass:
    """Narrow a DB string; the CHECK constraint guarantees the set."""
    assert value in VALID_DAMAGE_CLASSES
    return value  # pyright: ignore[reportReturnType]


def as_debris(value: str | None) -> Debris | None:
    if value is None:
        return None
    return value  # pyright: ignore[reportReturnType]


class LocationIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class LocationOut(BaseModel):
    lat: float
    lng: float


def location_or_none(lat: float | None, lng: float | None) -> LocationOut | None:
    if lat is None or lng is None:
        return None
    return LocationOut(lat=lat, lng=lng)
