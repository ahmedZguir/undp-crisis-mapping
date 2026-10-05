"""Unit tests for validate_photo — boundary cases for size and MIME."""

from __future__ import annotations

import pytest

from api.reports.photo import (
    MAX_PHOTO_BYTES,
    PhotoTooLargeError,
    UnsupportedPhotoTypeError,
    validate_photo,
)


def test_size_at_limit_is_allowed() -> None:
    validate_photo("image/jpeg", MAX_PHOTO_BYTES)


def test_size_one_byte_over_limit_is_rejected() -> None:
    with pytest.raises(PhotoTooLargeError):
        validate_photo("image/jpeg", MAX_PHOTO_BYTES + 1)


@pytest.mark.parametrize(
    "content_type",
    ["image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"],
)
def test_allowed_mime_types(content_type: str) -> None:
    validate_photo(content_type, 1024)


@pytest.mark.parametrize("content_type", ["image/gif", "application/pdf"])
def test_rejected_mime_types(content_type: str) -> None:
    with pytest.raises(UnsupportedPhotoTypeError):
        validate_photo(content_type, 1024)
