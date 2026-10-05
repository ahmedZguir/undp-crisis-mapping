"""`_row_to_item` degrades a missing photo to a null URL, not a 500.

A single report whose photo object is missing from storage must not take
down the whole history feed — the row's `photo_url` becomes `None` and the
list still returns. Pure service-level test: a fake signer that raises
`StorageError`, a `SimpleNamespace` row, no DB / no Supabase.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from api.core.storage import StorageError
from api.reports.history import CitizenReportsHistoryService


class _RaisingSigner:
    """Stand-in signer that fails the way Supabase does for a missing object."""

    async def sign_photo_url(self, photo_path: str, ttl_seconds: int) -> str:
        raise StorageError(
            f"Storage sign failed (400): Object not found ({photo_path}, {ttl_seconds})"
        )


class _OkSigner:
    async def sign_photo_url(self, photo_path: str, ttl_seconds: int) -> str:
        return f"https://signed.test/{photo_path}?ttl={ttl_seconds}"


def _row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "crisis_id": uuid.uuid4(),
        "crisis_name": "Test crisis",
        "crisis_status": "active",
        "created_at": datetime.now(UTC),
        "damage_class": "minimal",
        "description": None,
        "route_description": None,
        "report_lat": None,
        "report_lng": None,
        "infra_type": None,
        "infra_name": None,
        "crisis_type": None,
        "crisis_type_detailed": None,
        "debris": None,
        "building_id": None,
        "photo_path": "aa/bb/aabb.jpg",
        "client_submission_id": None,
        # report_quality LEFT JOIN columns; default to "no sidecar row".
        "q_report_id": None,
        "q_computed_at": None,
        "q_points": None,
        "q_has_photo": None,
        "q_has_description": None,
        "q_relevance_label": None,
        "q_damage_agreement": None,
        "q_is_duplicate_image": None,
        "q_verified": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _service(signer: Any) -> CitizenReportsHistoryService:
    # sessionmaker is unused by `_row_to_item`; pass None for this unit test.
    return CitizenReportsHistoryService(sessionmaker=None, url_signer=signer)  # type: ignore[arg-type]


async def test_missing_photo_degrades_to_null_url() -> None:
    item = await _service(_RaisingSigner())._row_to_item(_row())  # pyright: ignore[reportPrivateUsage]
    assert item.photo_url is None


async def test_present_photo_keeps_signed_url() -> None:
    item = await _service(_OkSigner())._row_to_item(  # pyright: ignore[reportPrivateUsage]
        _row(photo_path="cc/dd/ccdd.jpg")
    )
    assert item.photo_url == "https://signed.test/cc/dd/ccdd.jpg?ttl=900"


async def test_one_missing_photo_does_not_break_sibling_rows() -> None:
    """The whole point: a bad row coexists with good ones in one response."""
    svc = _service(_RaisingSigner())
    bad = await svc._row_to_item(_row())  # pyright: ignore[reportPrivateUsage]
    ok = await _service(_OkSigner())._row_to_item(  # pyright: ignore[reportPrivateUsage]
        _row(photo_path="ee/ff/eeff.jpg")
    )
    assert bad.photo_url is None
    assert ok.photo_url is not None
