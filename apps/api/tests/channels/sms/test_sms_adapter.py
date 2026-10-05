"""SmsGatewayAdapter payload shape and SIM pinning (the SIM with international credit)."""

from __future__ import annotations

import httpx
import pytest

from api.channels.sms.adapter import SmsGatewayAdapter


def _capture() -> tuple[httpx.AsyncClient, list[dict[str, object]]]:
    """An httpx client whose MockTransport records every POST body."""
    sent: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        sent.append(json.loads(request.content))
        return httpx.Response(200)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), sent


@pytest.mark.asyncio
async def test_send_includes_sim_number_when_configured() -> None:
    client, sent = _capture()
    adapter = SmsGatewayAdapter("https://x/3rdparty/v1", "u", "p", sim_number=2, client=client)

    await adapter.send_text("+966568112867", "hi")

    assert sent[0]["simNumber"] == 2
    assert sent[0]["phoneNumbers"] == ["+966568112867"]
    assert sent[0]["textMessage"] == {"text": "hi"}


@pytest.mark.asyncio
async def test_send_omits_sim_number_when_unset() -> None:
    client, sent = _capture()
    adapter = SmsGatewayAdapter("https://x/3rdparty/v1", "u", "p", client=client)

    await adapter.send_text("+97433885517", "hi")

    assert "simNumber" not in sent[0]


@pytest.mark.asyncio
async def test_legacy_fallback_keeps_sim_number() -> None:
    """On a 422 the adapter retries the flat body — the SIM must survive."""
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        bodies.append(json.loads(request.content))
        # Reject the new shape once, accept the legacy retry.
        return httpx.Response(422 if len(bodies) == 1 else 200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = SmsGatewayAdapter("https://x/3rdparty/v1", "u", "p", sim_number=2, client=client)

    await adapter.send_text("+966568112867", "hi")

    assert len(bodies) == 2
    assert bodies[1] == {"message": "hi", "phoneNumbers": ["+966568112867"], "simNumber": 2}
