"""Supabase Storage over REST with the service-role key.

Buckets are private; clients read through short-lived signed URLs. Photo keys
are content-addressed, so identical images share one blob.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import AsyncIterator
from typing import Protocol
from urllib.parse import quote

import httpx

from api.core.config import Settings, get_settings
from api.reports.photo import PhotoAddress

DEFAULT_SIGNED_URL_TTL_SECONDS = 15 * 60
"""Shared by the admin detail endpoint and the citizen history feed."""

_EXPORT_UPLOAD_CHUNK_BYTES = 1024 * 1024


class StorageError(RuntimeError):
    """Raised when Supabase Storage rejects a request."""


class StorageUploader(Protocol):
    async def upload_photo(self, content: bytes, content_type: str) -> str: ...


class PhotoUrlSigner(Protocol):
    async def sign_photo_url(self, photo_path: str, ttl_seconds: int) -> str: ...


class PhotoDownloader(Protocol):
    """Returns (bytes, content_type)."""

    async def download_photo(self, photo_path: str) -> tuple[bytes, str]: ...


class PhotoStorageDeleter(Protocol):
    async def delete_photo(self, photo_path: str) -> None: ...


class ReportPdfStore(Protocol):
    async def upload_report_pdf(self, content: bytes, key: str) -> str: ...

    async def sign_report_url(self, key: str, ttl_seconds: int) -> str: ...


class ExportPartStore(Protocol):
    async def upload_export_part(self, file_path: str, key: str) -> int: ...

    async def sign_export_url(
        self, key: str, ttl_seconds: int, download: str | None = None
    ) -> str: ...

    async def delete_export_part(self, key: str) -> None: ...


def free_disk_bytes(path: str) -> int:
    return shutil.disk_usage(path).free


class StorageClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def upload_photo(self, content: bytes, content_type: str) -> str:
        key = PhotoAddress.key_for(content, content_type)
        url = (
            f"{self._settings.supabase_url}/storage/v1/object/"
            f"{self._settings.supabase_storage_bucket}/{key}"
        )
        headers = {
            "Authorization": f"Bearer {self._settings.supabase_service_role_key}",
            "Content-Type": content_type,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            head = await client.head(url, headers=headers)
            if head.status_code == 200:
                return key
            response = await client.post(url, content=content, headers=headers)
        if response.status_code >= 300:
            # 409: a concurrent writer stored the same content under this key.
            if response.status_code == 409:
                return key
            raise StorageError(f"Storage upload failed ({response.status_code}): {response.text}")
        return key

    async def download_photo(self, photo_path: str) -> tuple[bytes, str]:
        url = (
            f"{self._settings.supabase_url}/storage/v1/object/"
            f"{self._settings.supabase_storage_bucket}/{photo_path}"
        )
        headers = {"Authorization": f"Bearer {self._settings.supabase_service_role_key}"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
        if response.status_code >= 300:
            raise StorageError(f"Storage download failed ({response.status_code}): {response.text}")
        content_type = response.headers.get("content-type", "application/octet-stream")
        return response.content, content_type

    async def delete_photo(self, photo_path: str) -> None:
        """Best-effort delete; 404 counts as success."""
        await self._delete(self._settings.supabase_storage_bucket, photo_path, label="Storage")

    async def sign_photo_url(self, photo_path: str, ttl_seconds: int) -> str:
        return await self._sign(
            self._settings.supabase_storage_bucket, photo_path, ttl_seconds, label="Storage"
        )

    async def upload_report_pdf(self, content: bytes, key: str) -> str:
        """Upsert under a caller-chosen key, so a retried upload overwrites."""
        url = (
            f"{self._settings.supabase_url}/storage/v1/object/"
            f"{self._settings.crisis_reports_bucket}/{key}"
        )
        headers = {
            "Authorization": f"Bearer {self._settings.supabase_service_role_key}",
            "Content-Type": "application/pdf",
            "x-upsert": "true",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, content=content, headers=headers)
        if response.status_code >= 300:
            raise StorageError(f"Report upload failed ({response.status_code}): {response.text}")
        return key

    async def sign_report_url(self, key: str, ttl_seconds: int) -> str:
        return await self._sign(
            self._settings.crisis_reports_bucket, key, ttl_seconds, label="Report"
        )

    async def upload_export_part(self, file_path: str, key: str) -> int:
        """Stream a zip part from disk in chunks (upserted); returns bytes uploaded."""
        size = os.path.getsize(file_path)
        url = (
            f"{self._settings.supabase_url}/storage/v1/object/"
            f"{self._settings.report_exports_bucket}/{key}"
        )
        headers = {
            "Authorization": f"Bearer {self._settings.supabase_service_role_key}",
            "Content-Type": "application/zip",
            "x-upsert": "true",
            "Content-Length": str(size),
        }

        async def _body() -> AsyncIterator[bytes]:
            with open(file_path, "rb") as fh:
                while True:
                    chunk = fh.read(_EXPORT_UPLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    yield chunk

        # No overall read/write timeout: a multi-GB part legitimately takes
        # minutes. The connect timeout still bounds a dead endpoint.
        timeout = httpx.Timeout(None, connect=30.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, content=_body(), headers=headers)
        if response.status_code >= 300:
            raise StorageError(
                f"Export part upload failed ({response.status_code}): {response.text}"
            )
        return size

    async def sign_export_url(self, key: str, ttl_seconds: int, download: str | None = None) -> str:
        """download sets the saved filename via Content-Disposition, since
        browsers ignore <a download> on cross-origin URLs."""
        url = await self._sign(
            self._settings.report_exports_bucket, key, ttl_seconds, label="Export"
        )
        if download:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}download={quote(download)}"
        return url

    async def delete_export_part(self, key: str) -> None:
        """404 counts as success; other failures are retried by the next sweep."""
        await self._delete(self._settings.report_exports_bucket, key, label="Export part")

    async def _sign(self, bucket: str, key: str, ttl_seconds: int, *, label: str) -> str:
        """Absolute signed URL on the browser-reachable supabase_public_url."""
        sign_url = f"{self._settings.supabase_url}/storage/v1/object/sign/{bucket}/{key}"
        headers = {
            "Authorization": f"Bearer {self._settings.supabase_service_role_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(sign_url, headers=headers, json={"expiresIn": ttl_seconds})
        if response.status_code >= 300:
            raise StorageError(f"{label} sign failed ({response.status_code}): {response.text}")
        signed = response.json().get("signedURL")
        if not isinstance(signed, str) or not signed:
            raise StorageError(f"{label} sign returned no signedURL: {response.text}")
        return f"{self._settings.supabase_public_url}/storage/v1{signed}"

    async def _delete(self, bucket: str, key: str, *, label: str) -> None:
        """404 counts as success."""
        url = f"{self._settings.supabase_url}/storage/v1/object/{bucket}/{key}"
        headers = {"Authorization": f"Bearer {self._settings.supabase_service_role_key}"}
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.delete(url, headers=headers)
        if response.status_code in (200, 204, 404):
            return
        raise StorageError(f"{label} delete failed ({response.status_code}): {response.text}")
