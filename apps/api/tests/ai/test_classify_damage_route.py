"""Route tests for `POST /ai/classify-damage`.

The route is exercised through the real FastAPI app with `get_app_state`
overridden to hand back a fake `AIClients` — so no DB, Redis-backed state,
or model server is touched. Covers the happy path + body shape, the
unconfigured (503) and upstream-error (503) degradations, and the
upload-validation mapping (415).
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import APITimeoutError, AsyncOpenAI

from api.ai.client import AIClients, ConfiguredClient
from api.core.app_state import get_app_state
from api.main import app

_IMAGE = b"\x89PNG\r\n\x1a\n-bytes"


class _FakeCompletions:
    def __init__(self, reply: str | None = None, *, raises: Exception | None = None) -> None:
        self._reply = reply
        self._raises = raises

    async def create(self, **_: Any) -> Any:
        if self._raises is not None:
            raise self._raises
        message = SimpleNamespace(content=self._reply)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _clients(completions: _FakeCompletions | None) -> AIClients:
    if completions is None:
        return AIClients(text=None, embedding=None, classifier=None)
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    classifier = ConfiguredClient(client=cast(AsyncOpenAI, fake_client), model="test-classifier")
    return AIClients(text=None, embedding=None, classifier=classifier)


def _use_clients(clients: AIClients) -> None:
    app.dependency_overrides[get_app_state] = lambda: SimpleNamespace(ai_clients=clients)


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    yield
    app.dependency_overrides.pop(get_app_state, None)


def test_classify_damage_happy_path() -> None:
    _use_clients(_clients(_FakeCompletions("partial")))
    client = TestClient(app)
    res = client.post("/ai/classify-damage", files={"photo": ("x.png", _IMAGE, "image/png")})
    assert res.status_code == 200, res.text
    assert res.json() == {"damage_class": "partial"}


def test_classify_damage_unconfigured_returns_503() -> None:
    _use_clients(_clients(None))
    client = TestClient(app)
    res = client.post("/ai/classify-damage", files={"photo": ("x.png", _IMAGE, "image/png")})
    assert res.status_code == 503, res.text
    assert res.json()["detail"] == "damage_classifier_unavailable"


def test_classify_damage_upstream_timeout_returns_503() -> None:
    timeout = APITimeoutError(request=httpx.Request("POST", "http://classifier"))
    _use_clients(_clients(_FakeCompletions(raises=timeout)))
    client = TestClient(app)
    res = client.post("/ai/classify-damage", files={"photo": ("x.png", _IMAGE, "image/png")})
    assert res.status_code == 503, res.text


def test_classify_damage_rejects_unsupported_type_415() -> None:
    _use_clients(_clients(_FakeCompletions("minimal")))
    client = TestClient(app)
    res = client.post("/ai/classify-damage", files={"photo": ("x.txt", b"hello", "text/plain")})
    assert res.status_code == 415, res.text
