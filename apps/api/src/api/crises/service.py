"""Crisis listing and validation. The reserved crisis sorts last, others newest first."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, cast

from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas import PublicVisibility
from api.schemas.crises import CrisisListItem


class CrisisNotFoundError(LookupError):
    """No row with the supplied `crisis_id`."""


class CrisisArchivedError(RuntimeError):
    """Row exists but its `status` is not `'active'`."""


@dataclass(frozen=True)
class CrisisRow:
    id: uuid.UUID
    name: str
    status: str
    created_at: datetime
    pmtiles_url: str | None = None
    overture_release_pinned: str | None = None
    geometry: dict[str, Any] | None = None
    # One of four modes, enforced by a DB CHECK.
    public_visibility: str = "aggregate_view"
    # Public infra-type whitelist for `buildings`/`full` modes; None means no filter.
    public_infra_types: list[str] | None = None


class CrisisLookup(Protocol):
    """DB access for crises.

    `_session` lets callers that already hold a session avoid a second pool checkout.
    """

    async def fetch_by_id(
        self,
        crisis_id: uuid.UUID,
        *,
        _session: AsyncSession | None = None,
    ) -> CrisisRow | None: ...

    async def fetch_active(self) -> list[CrisisRow]: ...

    async def fetch_form_by_id(
        self,
        crisis_id: uuid.UUID,
    ) -> tuple[dict[str, Any], int] | None:
        """(form_schema, form_version); kept off the row cache because the schema is large."""
        ...


class CrisisService:
    def __init__(self, lookup: CrisisLookup, reserved_name: str) -> None:
        self._lookup = lookup
        self._reserved_name = reserved_name

    async def require_exists(
        self,
        crisis_id: uuid.UUID,
        *,
        _session: AsyncSession | None = None,
    ) -> CrisisRow:
        """Existence-only check, ignoring status and visibility (admin surfaces)."""
        row = await self._lookup.fetch_by_id(crisis_id, _session=_session)
        if row is None:
            raise CrisisNotFoundError(str(crisis_id))
        return row

    async def require_active(
        self,
        crisis_id: uuid.UUID,
        *,
        _session: AsyncSession | None = None,
    ) -> None:
        row = await self.require_exists(crisis_id, _session=_session)
        if row.status != "active":
            raise CrisisArchivedError(str(crisis_id))

    async def require_public_visible(
        self,
        crisis_id: uuid.UUID,
        mode: str | None = None,
        *,
        _session: AsyncSession | None = None,
    ) -> CrisisRow:
        """Gate for public endpoints; the crisis must be active and publicly visible.

        With `mode`, visibility must be exactly that mode; without it, any mode but `none`.
        """
        row = await self.require_exists(crisis_id, _session=_session)
        if row.status != "active":
            raise CrisisNotFoundError(str(crisis_id))
        visible = row.public_visibility != "none" if mode is None else row.public_visibility == mode
        if not visible:
            raise CrisisNotFoundError(str(crisis_id))
        return row

    async def get_form(
        self,
        crisis_id: uuid.UUID,
    ) -> tuple[dict[str, Any], int] | None:
        """Return (form_schema, form_version) with locale maps unresolved, or None."""
        return await self._lookup.fetch_form_by_id(crisis_id)

    async def list_active(self) -> list[CrisisListItem]:
        rows = [r for r in await self._lookup.fetch_active() if r.status == "active"]
        reserved = [r for r in rows if r.name == self._reserved_name]
        others = sorted(
            (r for r in rows if r.name != self._reserved_name),
            key=lambda r: r.created_at,
            reverse=True,
        )
        ordered = others + reserved
        return [
            CrisisListItem(
                id=r.id,
                name=r.name,
                pmtiles_url=r.pmtiles_url,
                overture_release_pinned=r.overture_release_pinned,
                geometry=r.geometry,
                public_visibility=cast(PublicVisibility, r.public_visibility),
            )
            for r in ordered
        ]
