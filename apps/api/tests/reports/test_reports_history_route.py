"""Route-level tests for `GET /reports?client_id=…`.

Uses FastAPI's dependency override to swap the history service for a
stub, so the route's UUID coercion, query-param validation, and response
shape are exercised without DB or Storage.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.reports.routes import get_reports_history_service
from api.schemas.reports import (
    CitizenReportHistoryItem,
    CitizenReportHistoryResponse,
)


class _StubService:
    """Stand-in whose `list_by_client` returns a pre-seeded response."""

    def __init__(self, response: CitizenReportHistoryResponse) -> None:
        self._response = response
        self.calls: list[tuple[uuid.UUID, int]] = []

    async def list_by_client(
        self, client_id: uuid.UUID, limit: int = 100
    ) -> CitizenReportHistoryResponse:
        self.calls.append((client_id, limit))
        return self._response


def _empty_response() -> CitizenReportHistoryResponse:
    return CitizenReportHistoryResponse(items=[], total=0)


def _one_item_response() -> CitizenReportHistoryResponse:
    return CitizenReportHistoryResponse(
        items=[
            CitizenReportHistoryItem(
                id=uuid.uuid4(),
                crisis_id=uuid.uuid4(),
                crisis_name="Test crisis",
                crisis_status="active",
                created_at=datetime.now(UTC),
                damage_class="partial",
                photo_url="https://signed.example/photo?token=stub",
            )
        ],
        total=1,
    )


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def stub_service() -> Iterator[list[CitizenReportHistoryResponse]]:
    """Override the route's service. Tests push the response they want
    into the yielded list; only the last value is served."""
    bucket: list[CitizenReportHistoryResponse] = []

    def _override() -> _StubService:
        return _StubService(bucket[-1])

    app.dependency_overrides[get_reports_history_service] = _override
    try:
        yield bucket
    finally:
        app.dependency_overrides.pop(get_reports_history_service, None)


def _get(client: TestClient, **params: str | int) -> httpx.Response:
    return client.get("/reports", params=params)


def test_returns_empty_envelope_when_service_has_no_rows(
    client: TestClient, stub_service: list[CitizenReportHistoryResponse]
) -> None:
    stub_service.append(_empty_response())
    response = _get(client, client_id=str(uuid.uuid4()))
    assert response.status_code == 200, response.text
    assert response.json() == {"items": [], "total": 0}


def test_returns_items_and_total_from_service(
    client: TestClient, stub_service: list[CitizenReportHistoryResponse]
) -> None:
    stub_service.append(_one_item_response())
    response = _get(client, client_id=str(uuid.uuid4()))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["photo_url"] == "https://signed.example/photo?token=stub"


def test_missing_client_id_returns_422(client: TestClient) -> None:
    assert _get(client).status_code == 422


def test_malformed_client_id_returns_422(client: TestClient) -> None:
    assert _get(client, client_id="not-a-uuid").status_code == 422


def test_limit_above_cap_returns_422(
    client: TestClient, stub_service: list[CitizenReportHistoryResponse]
) -> None:
    stub_service.append(_empty_response())
    assert _get(client, client_id=str(uuid.uuid4()), limit=101).status_code == 422


def test_limit_below_one_returns_422(
    client: TestClient, stub_service: list[CitizenReportHistoryResponse]
) -> None:
    stub_service.append(_empty_response())
    assert _get(client, client_id=str(uuid.uuid4()), limit=0).status_code == 422


def test_explicit_limit_is_forwarded_to_service(
    client: TestClient, stub_service: list[CitizenReportHistoryResponse]
) -> None:
    stub_service.append(_empty_response())
    captured: dict[str, object] = {}

    def _override() -> _StubService:
        svc = _StubService(stub_service[-1])
        captured["svc"] = svc
        return svc

    app.dependency_overrides[get_reports_history_service] = _override
    try:
        client_id = uuid.uuid4()
        response = _get(client, client_id=str(client_id), limit=25)
        assert response.status_code == 200, response.text
        stub = captured["svc"]
        assert isinstance(stub, _StubService)
        assert stub.calls == [(client_id, 25)]
    finally:
        app.dependency_overrides.pop(get_reports_history_service, None)
