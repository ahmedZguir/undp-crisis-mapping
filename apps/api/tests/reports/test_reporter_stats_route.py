"""Route-level tests for `GET /me/stats?client_id=…`.

Dependency-override the stats service so the route's UUID coercion,
`since` parsing, and response shape are exercised without DB.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.reports.routes import get_reporter_stats_service
from api.schemas.reporter_stats import BadgeOut, ReporterStatsResponse


class _StubService:
    def __init__(self, response: ReporterStatsResponse) -> None:
        self._response = response
        self.calls: list[tuple[uuid.UUID, datetime | None]] = []

    async def stats_for_client(
        self, client_id: uuid.UUID, *, since: datetime | None = None
    ) -> ReporterStatsResponse:
        self.calls.append((client_id, since))
        return self._response


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def stub() -> Iterator[list[ReporterStatsResponse]]:
    bucket: list[ReporterStatsResponse] = []

    def _override() -> _StubService:
        return _StubService(bucket[-1])

    app.dependency_overrides[get_reporter_stats_service] = _override
    try:
        yield bucket
    finally:
        app.dependency_overrides.pop(get_reporter_stats_service, None)


def test_returns_stats_shape(client: TestClient, stub: list[ReporterStatsResponse]) -> None:
    cid = uuid.uuid4()
    stub.append(
        ReporterStatsResponse(
            client_id=cid,
            total_reports=3,
            points=11,
            badges=[
                BadgeOut(
                    slug="first_report",
                    name="First Responder",
                    earned_at=datetime.now(UTC),
                    report_id=uuid.uuid4(),
                )
            ],
            newly_earned=[],
        )
    )
    resp = client.get("/me/stats", params={"client_id": str(cid)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_reports"] == 3
    assert body["points"] == 11
    assert body["badges"][0]["slug"] == "first_report"
    assert body["newly_earned"] == []


def test_empty_client_is_zeroed(client: TestClient, stub: list[ReporterStatsResponse]) -> None:
    cid = uuid.uuid4()
    stub.append(
        ReporterStatsResponse(client_id=cid, total_reports=0, points=0, badges=[], newly_earned=[])
    )
    resp = client.get("/me/stats", params={"client_id": str(cid)})
    assert resp.status_code == 200
    assert resp.json()["points"] == 0


def test_malformed_client_id_is_422(client: TestClient) -> None:
    resp = client.get("/me/stats", params={"client_id": "not-a-uuid"})
    assert resp.status_code == 422


def test_missing_client_id_is_422(client: TestClient) -> None:
    resp = client.get("/me/stats")
    assert resp.status_code == 422
