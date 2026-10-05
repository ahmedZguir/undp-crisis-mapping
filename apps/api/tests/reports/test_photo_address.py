"""Pure unit tests for PhotoAddress.

`PhotoAddress.key_for(bytes, content_type)` is a deterministic, content-
addressed mapping. No I/O, no fixtures, no DB. The contract:

- Same bytes → same key, regardless of how often we call it.
- Different bytes → different keys.
- Each supported MIME maps to its expected extension.
- An unsupported MIME is rejected the same way `validate_photo` rejects it.
"""

from __future__ import annotations

import pytest

from api.reports.photo import PhotoAddress, UnsupportedPhotoTypeError


def test_same_bytes_same_mime_yield_identical_keys() -> None:
    a = PhotoAddress.key_for(b"hello world", "image/jpeg")
    b = PhotoAddress.key_for(b"hello world", "image/jpeg")
    assert a == b


def test_different_bytes_yield_different_keys() -> None:
    a = PhotoAddress.key_for(b"hello world", "image/jpeg")
    b = PhotoAddress.key_for(b"goodbye world", "image/jpeg")
    assert a != b


@pytest.mark.parametrize(
    ("content_type", "ext"),
    [
        ("image/jpeg", "jpg"),
        ("image/png", "png"),
        ("image/webp", "webp"),
        ("image/heic", "heic"),
        ("image/heif", "heif"),
    ],
)
def test_each_supported_mime_maps_to_expected_extension(content_type: str, ext: str) -> None:
    key = PhotoAddress.key_for(b"\x00\x01\x02", content_type)
    assert key.endswith(f".{ext}")


def test_key_shape_is_two_level_prefix_plus_full_hex_digest() -> None:
    """`ab/cd/<64 hex>.<ext>` — assert the structural invariant once."""
    key = PhotoAddress.key_for(b"abc", "image/jpeg")
    parts = key.split("/")
    assert len(parts) == 3
    assert len(parts[0]) == 2
    assert len(parts[1]) == 2
    name, _, ext = parts[2].rpartition(".")
    assert len(name) == 64
    assert ext == "jpg"
    # The two-level prefix is the leading 4 hex chars of the digest.
    assert name.startswith(parts[0] + parts[1])


def test_unsupported_mime_is_rejected() -> None:
    with pytest.raises(UnsupportedPhotoTypeError):
        PhotoAddress.key_for(b"abc", "image/gif")
