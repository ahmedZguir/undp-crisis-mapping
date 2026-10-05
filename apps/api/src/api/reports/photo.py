"""Photo MIME/size validation and content-addressed storage keys.

Keys are `ab/cd/<sha256>.<ext>`: the prefix partitions listings, and the full digest
lets a key verify its content.
"""

from __future__ import annotations

import hashlib

ALLOWED_MIME_TYPES: frozenset[str] = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
)
MAX_PHOTO_BYTES: int = 10 * 1024 * 1024  # 10 MB


class PhotoValidationError(Exception):
    """Base class for photo validation failures."""


class PhotoTooLargeError(PhotoValidationError):
    pass


class UnsupportedPhotoTypeError(PhotoValidationError):
    pass


def validate_photo(content_type: str, size: int) -> None:
    if content_type not in ALLOWED_MIME_TYPES:
        raise UnsupportedPhotoTypeError(f"Unsupported photo content type: {content_type!r}")
    if size > MAX_PHOTO_BYTES:
        raise PhotoTooLargeError(f"Photo size {size} exceeds {MAX_PHOTO_BYTES} bytes")


_EXTENSIONS: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/heic": "heic",
    "image/heif": "heif",
}


class PhotoAddress:
    @staticmethod
    def key_for(content: bytes, content_type: str) -> str:
        ext = _EXTENSIONS.get(content_type)
        if ext is None:
            raise UnsupportedPhotoTypeError(f"Unsupported photo content type: {content_type!r}")
        digest = hashlib.sha256(content).hexdigest()
        return f"{digest[0:2]}/{digest[2:4]}/{digest}.{ext}"
