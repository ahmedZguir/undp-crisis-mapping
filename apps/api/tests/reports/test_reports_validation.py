"""Route-level error mapping for POST /reports.

Uses FastAPI's dependency override to swap the report-submission service for a
stub, so the route's pre-service validation (JSON parse + Pydantic) and its
typed-error → HTTP-status mapping are exercised without DB or Storage.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.reports.photo import PhotoTooLargeError, UnsupportedPhotoTypeError
from api.reports.routes import get_report_service
from api.reports.service import ReportContentError
from api.schemas.reports import ReportSubmitPayload

FIXTURE = Path(__file__).parent.parent / "fixtures" / "tiny.jpg"

_VALID_CRISIS_ID = str(uuid.uuid4())


class _StubService:
    """Stand-in whose `submit` raises a chosen error."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def submit(
        self,
        photo_content: bytes | None,
        photo_content_type: str | None,
        payload: ReportSubmitPayload,
    ) -> None:
        raise self._exc


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Lifespan-aware TestClient. The `with` block runs FastAPI's startup so
    `app.state.container` is populated before any request hits the dependency
    graph, even when the test will override the service."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def stub_service() -> Iterator[list[Exception]]:
    """Override the route's service with a stub. Each test sets the exception
    to raise by appending to the yielded list (only the last value is used)."""
    bucket: list[Exception] = []

    def _override() -> _StubService:
        return _StubService(bucket[-1])

    app.dependency_overrides[get_report_service] = _override
    try:
        yield bucket
    finally:
        app.dependency_overrides.pop(get_report_service, None)


def _post(
    client: TestClient,
    payload: dict[str, object] | str,
    mime: str = "image/jpeg",
) -> httpx.Response:
    data = payload if isinstance(payload, str) else json.dumps(payload)
    files = {
        "photo": ("tiny.jpg", FIXTURE.read_bytes(), mime),
        "data": (None, data, "application/json"),
    }
    return client.post("/reports", files=files)


def test_malformed_json_returns_400(client: TestClient, stub_service: list[Exception]) -> None:
    stub_service.append(RuntimeError("should not be called"))
    response = _post(client, "{not-valid-json")
    assert response.status_code == 400
    assert "Invalid JSON" in response.json()["detail"]


def test_invalid_damage_class_returns_400(
    client: TestClient, stub_service: list[Exception]
) -> None:
    stub_service.append(RuntimeError("should not be called"))
    response = _post(client, {"crisis_id": _VALID_CRISIS_ID, "damage_class": "totaled"})
    assert response.status_code == 400
    detail: list[dict[str, object]] = response.json()["detail"]
    assert isinstance(detail, list)
    assert any("damage_class" in str(err.get("loc", "")) for err in detail)


def test_unsupported_mime_returns_415(client: TestClient, stub_service: list[Exception]) -> None:
    stub_service.append(UnsupportedPhotoTypeError("nope"))
    response = _post(
        client, {"crisis_id": _VALID_CRISIS_ID, "damage_class": "partial"}, mime="image/gif"
    )
    assert response.status_code == 415


def test_oversized_photo_returns_413(client: TestClient, stub_service: list[Exception]) -> None:
    stub_service.append(PhotoTooLargeError("too big"))
    response = _post(client, {"crisis_id": _VALID_CRISIS_ID, "damage_class": "partial"})
    assert response.status_code == 413


def test_report_content_error_returns_422(
    client: TestClient, stub_service: list[Exception]
) -> None:
    """The service's minimum-content gate maps to HTTP 422 at the route."""
    stub_service.append(ReportContentError("photo_or_description_required"))
    response = _post(client, {"crisis_id": _VALID_CRISIS_ID, "damage_class": "partial"})
    assert response.status_code == 422
    assert response.json()["detail"] == "photo_or_description_required"


def test_description_only_post_without_photo_part_reaches_service(
    client: TestClient, stub_service: list[Exception]
) -> None:
    """A multipart POST with no `photo` file part is accepted by the route and
    delegated to the service (it no longer 400s on a missing photo). We prove
    delegation by observing the service-raised 422 rather than a route 400."""
    stub_service.append(ReportContentError("location_or_route_required"))
    files = {
        "data": (
            None,
            json.dumps(
                {
                    "crisis_id": _VALID_CRISIS_ID,
                    "damage_class": "partial",
                    "description": "Wall crack on south face",
                }
            ),
            "application/json",
        ),
    }
    response = client.post("/reports", files=files)
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "location_or_route_required"


def test_invalid_debris_value_returns_400(
    client: TestClient, stub_service: list[Exception]
) -> None:
    stub_service.append(RuntimeError("should not be called"))
    response = _post(
        client, {"crisis_id": _VALID_CRISIS_ID, "damage_class": "partial", "debris": "maybe"}
    )
    assert response.status_code == 400
    detail: list[dict[str, object]] = response.json()["detail"]
    assert isinstance(detail, list)
    assert any("debris" in str(err.get("loc", "")) for err in detail)


def test_missing_crisis_id_returns_400(client: TestClient, stub_service: list[Exception]) -> None:
    stub_service.append(RuntimeError("should not be called"))
    response = _post(client, {"damage_class": "partial"})
    assert response.status_code == 400
    detail: list[dict[str, object]] = response.json()["detail"]
    assert isinstance(detail, list)
    assert any("crisis_id" in str(err.get("loc", "")) for err in detail)


def test_non_uuid_crisis_id_returns_400(client: TestClient, stub_service: list[Exception]) -> None:
    stub_service.append(RuntimeError("should not be called"))
    response = _post(client, {"crisis_id": "not-a-uuid", "damage_class": "partial"})
    assert response.status_code == 400
    detail: list[dict[str, object]] = response.json()["detail"]
    assert isinstance(detail, list)
    assert any("crisis_id" in str(err.get("loc", "")) for err in detail)


@pytest.mark.parametrize(
    "field",
    [
        "infra_type",
        "infra_name",
        "crisis_type",
        "crisis_type_detailed",
        "debris",
    ],
)
def test_expanded_field_is_optional(field: str) -> None:
    """Every expanded field defaults to None — omitting it must validate."""
    full: dict[str, object] = {
        "crisis_id": _VALID_CRISIS_ID,
        "damage_class": "partial",
        "infra_type": ["bridge"],
        "infra_name": "x",
        "crisis_type": "natural_hazards",
        "crisis_type_detailed": "earthquake",
        "debris": "yes",
    }
    full.pop(field)
    payload = ReportSubmitPayload.model_validate(full)
    assert getattr(payload, field) is None
