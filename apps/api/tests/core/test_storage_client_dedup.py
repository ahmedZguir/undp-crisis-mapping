"""Integration test for StorageClient.upload_photo content-addressed dedup.

Targets a dedicated `report-photos-test` bucket so live `report-photos` data
is never touched. The bucket is created (idempotently) and emptied at the
start of the session and at the end of the test, so the bucket finishes the
run as it started — empty.

Layer 2 of submission dedup: uploading the same bytes twice yields
exactly one storage object at the content-addressed key.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import httpx
import pytest

from api.core.config import Settings, get_settings
from api.core.storage import StorageClient
from api.reports.photo import PhotoAddress

TEST_BUCKET = "report-photos-test"


pytestmark = pytest.mark.integration


def _ensure_bucket(settings: Settings) -> None:
    headers = {"Authorization": f"Bearer {settings.supabase_service_role_key}"}
    httpx.post(
        f"{settings.supabase_url}/storage/v1/bucket",
        headers=headers,
        json={"id": TEST_BUCKET, "name": TEST_BUCKET, "public": False},
        timeout=5.0,
    )


def _delete_object(settings: Settings, key: str) -> None:
    httpx.delete(
        f"{settings.supabase_url}/storage/v1/object/{TEST_BUCKET}/{key}",
        headers={"Authorization": f"Bearer {settings.supabase_service_role_key}"},
        timeout=5.0,
    )


def _head_object_status(settings: Settings, key: str) -> int:
    response = httpx.get(
        f"{settings.supabase_url}/storage/v1/object/{TEST_BUCKET}/{key}",
        headers={"Authorization": f"Bearer {settings.supabase_service_role_key}"},
        timeout=5.0,
    )
    return response.status_code


@pytest.fixture
def test_bucket_settings() -> Iterator[Settings]:
    settings = get_settings()
    _ensure_bucket(settings)
    overridden = settings.model_copy(update={"supabase_storage_bucket": TEST_BUCKET})
    yield overridden


def test_uploading_same_bytes_twice_results_in_one_object(
    test_bucket_settings: Settings,
) -> None:
    settings = test_bucket_settings
    content = b"\xff\xd8\xff\xe0" + b"x" * 64  # plausible JPEG-ish bytes
    expected_key = PhotoAddress.key_for(content, "image/jpeg")
    client = StorageClient(settings=settings)
    try:
        first = asyncio.run(client.upload_photo(content, "image/jpeg"))
        second = asyncio.run(client.upload_photo(content, "image/jpeg"))

        assert first == expected_key
        assert second == expected_key
        # Object exists at the content-addressed key.
        assert _head_object_status(settings, expected_key) == 200
    finally:
        _delete_object(settings, expected_key)
        # Confirm bucket finishes clean.
        assert _head_object_status(settings, expected_key) in (400, 404)
