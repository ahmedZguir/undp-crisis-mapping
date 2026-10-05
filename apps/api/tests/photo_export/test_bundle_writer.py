"""Unit tests for the photo-export bundle mechanics that need no DB.

Covers the two pieces with real edge cases: the `photos/<id>.<ext>` naming
(mirrored by the manifest's SQL `photo_file` column) and `_BundleWriter`'s
part rotation + manifest entry + streamed upload. The DB-backed resolver /
manifest queries are exercised by the integration suite.
"""

from __future__ import annotations

import io
import uuid
import zipfile

import pytest

from api.photo_export.job import _BundleWriter
from api.photo_export.resolver import export_photo_entry_name


def test_export_photo_entry_name_extensions() -> None:
    rid = uuid.UUID("11111111-1111-1111-1111-111111111111")
    assert export_photo_entry_name(rid, "ab/cd/deadbeef.jpg") == f"photos/{rid}.jpg"
    # Uppercased extension is normalised; nested path is ignored.
    assert export_photo_entry_name(rid, "ab/cd/deadbeef.PNG") == f"photos/{rid}.png"
    # No extension falls back to jpg.
    assert export_photo_entry_name(rid, "ab/cd/deadbeef") == f"photos/{rid}.jpg"
    # Photoless report has no entry.
    assert export_photo_entry_name(rid, None) is None


class _RecordingStore:
    """Fake `ExportPartStore` that snapshots each uploaded part's bytes so the
    test can assert the zip is valid after the writer deletes the temp file."""

    def __init__(self) -> None:
        self.parts: dict[str, bytes] = {}

    async def upload_export_part(self, file_path: str, key: str) -> int:
        with open(file_path, "rb") as fh:
            data = fh.read()
        self.parts[key] = data
        return len(data)

    async def sign_export_url(self, key: str, ttl_seconds: int) -> str:  # pragma: no cover
        return f"signed://{key}?ttl={ttl_seconds}"

    async def delete_export_part(self, key: str) -> None:  # pragma: no cover
        self.parts.pop(key, None)


@pytest.mark.anyio
async def test_bundle_writer_rotates_parts_and_writes_valid_zips(tmp_path) -> None:
    crisis_id = uuid.uuid4()
    export_id = uuid.uuid4()
    store = _RecordingStore()
    # Tiny part cap so a few photos force several parts.
    writer = _BundleWriter(
        scratch_dir=str(tmp_path),
        crisis_id=crisis_id,
        export_id=export_id,
        store=store,
        part_size_bytes=2048,
    )

    # Part 1 leads with a manifest entry written via the streaming sink.
    with writer.open_entry("manifest.csv") as dest:
        dest.write(b"report_id,photo_file\n")
        dest.write(b"abc,photos/abc.jpg\n")

    blob = b"x" * 900
    names = [f"photos/{uuid.uuid4()}.jpg" for _ in range(6)]
    for name in names:
        await writer.add_photo(name, blob)
    await writer.finish()

    # Several parts were produced (900-byte photos against a 2 KB cap).
    assert len(writer.parts) >= 2
    assert writer.total_bytes == sum(len(b) for b in store.parts.values())
    assert sum(int(p["photo_count"]) for p in writer.parts) == len(names)

    # Every uploaded part is a valid, independently-openable zip.
    seen_photos: set[str] = set()
    manifest_seen = False
    for part in writer.parts:
        data = store.parts[part["key"]]
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            assert zf.testzip() is None
            for entry in zf.namelist():
                if entry == "manifest.csv":
                    manifest_seen = True
                    assert zf.read(entry) == b"report_id,photo_file\nabc,photos/abc.jpg\n"
                else:
                    seen_photos.add(entry)

    assert manifest_seen, "manifest must live in part 1"
    assert seen_photos == set(names)
