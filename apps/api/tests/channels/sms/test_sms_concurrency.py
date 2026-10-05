"""Per-sender serialization of conversation turns (``_SenderLocks``)."""

from __future__ import annotations

import asyncio
from typing import cast

import pytest

from api.channels.sms.flow import SmsTemplateFlow
from api.channels.sms.routes import _dispatch_bot


class _OverlapDetectingFlow:
    """Records the peak number of turns running at once."""

    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.turns = 0

    async def handle(self, from_e164: str, body: str) -> None:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        # Yield so a concurrently-scheduled turn gets a chance to interleave.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.turns += 1
        self.active -= 1


@pytest.mark.asyncio
async def test_same_sender_turns_are_serialized() -> None:
    flow = _OverlapDetectingFlow()
    f = cast("SmsTemplateFlow", flow)
    await asyncio.gather(
        _dispatch_bot(f, "+97400000001", "a"),
        _dispatch_bot(f, "+97400000001", "b"),
    )
    assert flow.turns == 2
    assert flow.max_active == 1  # the two turns never overlapped


@pytest.mark.asyncio
async def test_different_senders_run_concurrently() -> None:
    flow = _OverlapDetectingFlow()
    f = cast("SmsTemplateFlow", flow)
    await asyncio.gather(
        _dispatch_bot(f, "+97400000001", "a"),
        _dispatch_bot(f, "+97400000002", "b"),
    )
    assert flow.turns == 2
    assert flow.max_active == 2  # distinct senders are not blocked by each other
