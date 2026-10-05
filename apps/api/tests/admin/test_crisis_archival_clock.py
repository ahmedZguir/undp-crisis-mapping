"""Unit tests for the retention-clock writer `apply_archival_clock`.

`crises.archived_at` is the anchor the data-retention purge counts from, so
its write contract matters: stamp on entering `archived`, clear on leaving it,
and never restart the clock on a re-archive. These pin that contract without a
DB.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from api.admin.crisis_writer import apply_archival_clock
from api.schemas.crises import CrisisPatchPayload


def test_stamps_archived_at_on_transition_to_archived() -> None:
    payload = CrisisPatchPayload(status="archived")
    updates: dict[str, Any] = {"status": "archived"}
    apply_archival_clock(payload=payload, updates=updates, current_status="active")
    assert isinstance(updates["archived_at"], datetime)


def test_clears_archived_at_on_unarchive() -> None:
    payload = CrisisPatchPayload(status="active")
    updates: dict[str, Any] = {"status": "active"}
    apply_archival_clock(payload=payload, updates=updates, current_status="archived")
    assert updates["archived_at"] is None


def test_no_reset_on_re_archive() -> None:
    # Re-archiving an already-archived crisis must NOT restart the retention
    # window — leave `archived_at` untouched.
    payload = CrisisPatchPayload(status="archived")
    updates: dict[str, Any] = {"status": "archived"}
    apply_archival_clock(payload=payload, updates=updates, current_status="archived")
    assert "archived_at" not in updates


def test_noop_when_status_not_in_payload() -> None:
    # A PATCH that does not touch status leaves the clock alone.
    payload = CrisisPatchPayload(name="renamed only")
    updates: dict[str, Any] = {"name": "renamed only"}
    apply_archival_clock(payload=payload, updates=updates, current_status="archived")
    assert "archived_at" not in updates
