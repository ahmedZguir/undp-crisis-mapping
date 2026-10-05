"""Unit tests for CrisisService against a fake CrisisLookup.

The service owns the ordering rule (reserved row pinned last, others by
created_at DESC) and the typed-error mapping for validate(). The fake
implements the Protocol so we can pin both behaviors down without a DB.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from api.crises.service import (
    CrisisArchivedError,
    CrisisNotFoundError,
    CrisisRow,
    CrisisService,
)

RESERVED = "Other / Unspecified"


class FakeLookup:
    def __init__(self, rows: list[CrisisRow]) -> None:
        self._rows = list(rows)

    async def fetch_by_id(
        self,
        crisis_id: uuid.UUID,
        *,
        _session: AsyncSession | None = None,
    ) -> CrisisRow | None:
        for row in self._rows:
            if row.id == crisis_id:
                return row
        return None

    async def fetch_active(self) -> list[CrisisRow]:
        return [r for r in self._rows if r.status == "active"]

    async def fetch_form_by_id(
        self,
        crisis_id: uuid.UUID,
    ) -> tuple[dict[str, object], int] | None:
        return None


def _service(rows: list[CrisisRow]) -> CrisisService:
    return CrisisService(FakeLookup(rows), reserved_name=RESERVED)


def _row(
    name: str = "op",
    status: str = "active",
    created_at: datetime | None = None,
) -> CrisisRow:
    return CrisisRow(
        id=uuid.uuid4(),
        name=name,
        status=status,
        created_at=created_at or datetime(2026, 5, 1, tzinfo=UTC),
    )


async def test_require_active_returns_none() -> None:
    row = _row(name="Aleppo earthquake", status="active")
    service = _service([row])

    result = await service.require_active(row.id)

    assert result is None


async def test_require_active_unknown_raises_not_found() -> None:
    service = _service([])

    with pytest.raises(CrisisNotFoundError):
        await service.require_active(uuid.uuid4())


async def test_require_active_archived_raises_archived() -> None:
    row = _row(name="Old response", status="archived")
    service = _service([row])

    with pytest.raises(CrisisArchivedError):
        await service.require_active(row.id)


async def test_list_active_returns_id_and_name() -> None:
    row = _row(name="Aleppo earthquake", status="active")
    service = _service([row])

    items = await service.list_active()

    assert len(items) == 1
    assert items[0].id == row.id
    assert items[0].name == "Aleppo earthquake"


async def test_list_active_filters_archived_rows() -> None:
    """Service trusts the lookup to filter, but should not include archived rows
    even if a lookup returned them — defense in depth."""

    class LeakyLookup:
        async def fetch_by_id(
            self,
            crisis_id: uuid.UUID,
            *,
            _session: AsyncSession | None = None,
        ) -> CrisisRow | None:
            return None

        async def fetch_active(self) -> list[CrisisRow]:
            return [
                _row(name="Active", status="active"),
                _row(name="Archived", status="archived"),
            ]

        async def fetch_form_by_id(
            self,
            crisis_id: uuid.UUID,
        ) -> tuple[dict[str, object], int] | None:
            return None

    service = CrisisService(LeakyLookup(), reserved_name=RESERVED)
    items = await service.list_active()

    assert [i.name for i in items] == ["Active"]


async def test_list_active_orders_non_reserved_by_created_at_desc() -> None:
    base = datetime(2026, 5, 1, tzinfo=UTC)
    older = _row(name="Older crisis", status="active", created_at=base)
    newer = _row(name="Newer crisis", status="active", created_at=base + timedelta(days=2))
    middle = _row(name="Middle crisis", status="active", created_at=base + timedelta(days=1))
    service = _service([older, newer, middle])

    items = await service.list_active()

    assert [i.name for i in items] == ["Newer crisis", "Middle crisis", "Older crisis"]


async def test_list_active_pins_reserved_row_last_regardless_of_created_at() -> None:
    base = datetime(2026, 5, 1, tzinfo=UTC)
    # Reserved row is the *newest* — would come first under created_at DESC alone.
    reserved = _row(name=RESERVED, status="active", created_at=base + timedelta(days=10))
    op_a = _row(name="Op A", status="active", created_at=base + timedelta(days=2))
    op_b = _row(name="Op B", status="active", created_at=base + timedelta(days=5))
    service = _service([reserved, op_a, op_b])

    items = await service.list_active()

    assert [i.name for i in items] == ["Op B", "Op A", RESERVED]


async def test_list_active_returns_empty_when_no_active_rows() -> None:
    service = _service([_row(name="Old", status="archived")])

    items = await service.list_active()

    assert items == []
