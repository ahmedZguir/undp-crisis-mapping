"""Twilio media download: media URLs need account basic auth; validated like a PWA upload."""

from __future__ import annotations

import httpx

from api.core.http import http_request
from api.reports.photo import (
    ALLOWED_MIME_TYPES,
    MAX_PHOTO_BYTES,
    PhotoTooLargeError,
    UnsupportedPhotoTypeError,
)


class MediaDownloadError(RuntimeError):
    pass


async def download_twilio_media(
    url: str,
    *,
    account_sid: str,
    auth_token: str,
    client: httpx.AsyncClient | None = None,
) -> tuple[bytes, str]:
    response = await http_request(
        client,
        "GET",
        url,
        timeout=30.0,
        auth=(account_sid, auth_token),
        follow_redirects=True,
    )

    if response.status_code >= 300:
        raise MediaDownloadError(f"Twilio media fetch failed ({response.status_code})")

    mime = (response.headers.get("content-type") or "").split(";")[0].strip()
    content = response.content

    if mime not in ALLOWED_MIME_TYPES:
        raise UnsupportedPhotoTypeError(f"Unsupported photo content type: {mime!r}")
    if len(content) > MAX_PHOTO_BYTES:
        raise PhotoTooLargeError(f"Photo size {len(content)} exceeds {MAX_PHOTO_BYTES} bytes")
    return content, mime
