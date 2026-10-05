"""WhatsApp webhook error handling: residents never see exception text."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from api.channels.sessions import Session
from api.channels.whatsapp import routes
from api.channels.whatsapp.flow import InboundMessage
from api.channels.whatsapp.messages import strings_for
from api.main import app

FROM = "+97455550000"


class _FakeMetaAdapter:
    def __init__(self) -> None:
        self.texts: list[tuple[str, str]] = []

    def validate_signature(self, raw: bytes, signature: str | None) -> bool:
        return True

    async def send_text(self, to: str, body: str) -> None:
        self.texts.append((to, body))


class _FailingFlow:
    async def handle(self, msg: InboundMessage) -> None:
        raise RuntimeError("submitReport failed: 422 {'detail': 'internal crisis_id=...'}")


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> Iterator[_FakeMetaAdapter]:
    fake = _FakeMetaAdapter()
    monkeypatch.setattr(routes, "_get_meta_adapter", lambda: fake)
    app.dependency_overrides[routes._get_flow_meta] = lambda: _FailingFlow()  # pyright: ignore[reportPrivateUsage]
    yield fake
    app.dependency_overrides.pop(routes._get_flow_meta, None)  # pyright: ignore[reportPrivateUsage]
    routes._get_sessions.cache_clear()  # pyright: ignore[reportPrivateUsage]


def _text_payload() -> dict[str, object]:
    message: dict[str, object] = {
        "from": FROM.lstrip("+"),
        "id": f"wamid.{uuid.uuid4()}",
        "type": "text",
        "text": {"body": "hello"},
    }
    return {"entry": [{"changes": [{"value": {"messages": [message]}}]}]}


@pytest.mark.asyncio
async def test_meta_flow_failure_sends_localized_generic_message(
    adapter: _FakeMetaAdapter,
) -> None:
    session = Session(phone_e164=FROM)
    session.language = "ar"
    await routes._get_sessions().upsert(session)  # pyright: ignore[reportPrivateUsage]

    with TestClient(app) as client:
        response = client.post("/whatsapp/webhook/meta", json=_text_payload())

    assert response.status_code == 200
    assert adapter.texts == [(FROM, strings_for("ar").flow_failed)]
