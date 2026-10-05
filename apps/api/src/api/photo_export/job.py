"""Arq job that builds a photo bundle for a pre-created `report_exports` row.

Photos stream into zip parts on local scratch; each part is uploaded and deleted
as it fills, so peak scratch is about one part.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import tempfile
import uuid
import zipfile
from collections.abc import Callable, Generator
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.ai.client import AIClients
from api.ai.embeddings import embed_query
from api.core.config import Settings, get_settings
from api.core.storage import ExportPartStore, PhotoDownloader, StorageError, free_disk_bytes
from api.photo_export import manifest as manifest_mod
from api.photo_export.resolver import (
    EST_PHOTO_BYTES,
    count_match_set,
    export_photo_entry_name,
    request_from_filters,
    stream_photo_rows,
)
from api.workers.context import ai_clients_from_ctx, sessionmaker_from_ctx, storage_from_ctx
from api.workers.errors import format_terminal_error
from api.workers.job_rows import JobRow

logger = logging.getLogger(__name__)
_DISK_FULL_MESSAGE = "Not enough space on server."
_PROGRESS_EVERY = 200
_MAX_SKIP_LOG = 20

PHASE_RESOLVING = "resolving"
PHASE_STREAMING = "streaming"
PHASE_FINALISING = "finalising"


class ExportDiskError(RuntimeError):
    """Expected outcome: the row ends 'failed' with a user-facing message and is not re-raised."""


async def export_crisis_photos(ctx: dict[str, object], export_id: str) -> None:
    settings = get_settings()
    await run_export_crisis_photos(
        export_id=uuid.UUID(export_id),
        sessionmaker=sessionmaker_from_ctx(ctx),
        store=storage_from_ctx(ctx),
        downloader=storage_from_ctx(ctx),
        clients=ai_clients_from_ctx(ctx),
        settings=settings,
        scratch_root=settings.export_scratch_dir,
    )


async def run_export_crisis_photos(
    *,
    export_id: uuid.UUID,
    sessionmaker: async_sessionmaker[AsyncSession],
    store: ExportPartStore,
    downloader: PhotoDownloader,
    clients: AIClients,
    settings: Settings,
    free_disk: Callable[[str], int] = free_disk_bytes,
    scratch_root: str | None = None,
) -> None:
    """Any exception marks the row 'failed' and re-raises, except a disk-guard trip."""
    loaded = await _load_row(sessionmaker, export_id)
    if loaded is None:
        logger.warning("export_crisis_photos: row %s vanished; nothing to do", export_id)
        return

    crisis_id, filters, fmt, scope, created_by_email = loaded
    job_row = JobRow(sessionmaker, "report_exports", export_id, "export_crisis_photos export_id")
    scratch_dir = tempfile.mkdtemp(prefix="export-", dir=scratch_root)
    try:
        request = request_from_filters(filters)
        query_vector: list[float] | None = None
        if request.query and request.query.strip():
            embedding = await embed_query(request.query.strip(), client=clients.embedding)
            query_vector = embedding.vector

        await job_row.set_phase(PHASE_RESOLVING)
        async with sessionmaker() as session:
            counts = await count_match_set(
                session, crisis_id=crisis_id, request=request, query_vector=query_vector
            )
            crisis_name = await _fetch_crisis_name(session, crisis_id)

        # Scratch shares the disk that backs Supabase storage; check before streaming.
        estimate = counts.with_photo * EST_PHOTO_BYTES
        free = free_disk(scratch_dir)
        if free <= estimate + settings.export_disk_headroom_bytes:
            logger.warning(
                "export_crisis_photos %s: disk guard tripped (free=%d, estimate=%d, headroom=%d)",
                export_id,
                free,
                estimate,
                settings.export_disk_headroom_bytes,
            )
            raise ExportDiskError(_DISK_FULL_MESSAGE)

        await job_row.update("progress_total = :t, progress_count = 0", {"t": counts.with_photo})
        await job_row.set_phase(PHASE_STREAMING)

        generated_at = datetime.now(UTC).isoformat()
        writer = _BundleWriter(
            scratch_dir=scratch_dir,
            crisis_id=crisis_id,
            export_id=export_id,
            store=store,
            part_size_bytes=settings.export_part_size_bytes,
        )

        writer.write_bytes(
            "export_manifest.json",
            manifest_mod.build_export_manifest_json(
                export_id=export_id,
                crisis_id=crisis_id,
                crisis_name=crisis_name,
                created_by_email=created_by_email,
                generated_at=generated_at,
                scope=scope,
                fmt=fmt,
                request=request,
                report_count=counts.total,
                photo_count=counts.with_photo,
            ),
        )
        with writer.open_entry(manifest_mod.manifest_filename(fmt)) as dest:
            if fmt == "geojson":
                metadata = manifest_mod.build_geojson_metadata(
                    crisis_id=crisis_id,
                    crisis_name=crisis_name,
                    request=request,
                    scope=scope,
                    report_count=counts.total,
                    generated_at=generated_at,
                )
                await manifest_mod.write_geojson_manifest(
                    dest,
                    sessionmaker=sessionmaker,
                    crisis_id=crisis_id,
                    request=request,
                    query_vector=query_vector,
                    metadata=metadata,
                )
            else:
                await manifest_mod.write_csv_manifest(
                    dest,
                    sessionmaker=sessionmaker,
                    crisis_id=crisis_id,
                    request=request,
                    query_vector=query_vector,
                )

        written = 0
        skipped = 0
        async for row in stream_photo_rows(
            sessionmaker, crisis_id=crisis_id, request=request, query_vector=query_vector
        ):
            name = export_photo_entry_name(row.id, row.photo_path)
            if name is None:  # photo_only filter guarantees a path
                continue
            try:
                data, _ = await downloader.download_photo(row.photo_path)
            except StorageError as exc:
                # A missing blob should not sink the export; the manifest still lists it.
                skipped += 1
                if skipped <= _MAX_SKIP_LOG:
                    logger.warning(
                        "export_crisis_photos %s: skipping unreadable photo report=%s path=%s (%s)",
                        export_id,
                        row.id,
                        row.photo_path,
                        exc,
                    )
                    if skipped == _MAX_SKIP_LOG:
                        logger.warning(
                            "export_crisis_photos %s: further skipped-photo warnings suppressed",
                            export_id,
                        )
                continue
            await writer.add_photo(name, data)
            written += 1
            if written % _PROGRESS_EVERY == 0:
                await job_row.try_update("progress", "progress_count = :c", {"c": written})

        await writer.finish()
        if skipped:
            logger.info(
                "export_crisis_photos %s: bundled %d photos, skipped %d unreadable",
                export_id,
                written,
                skipped,
            )

        await job_row.set_phase(PHASE_FINALISING)
        await job_row.mark_succeeded(
            "parts = cast(:parts as jsonb), photo_count = :photo_count, "
            "total_bytes = :total_bytes, progress_count = :photo_count, "
            "expires_at = now() + make_interval(hours => :hours)",
            {
                "parts": json.dumps(writer.parts),
                "photo_count": written,
                "total_bytes": writer.total_bytes,
                "hours": settings.export_retention_hours,
            },
        )
    except ExportDiskError as exc:
        await job_row.safe_mark_failed(str(exc))
    except BaseException as exc:
        logger.exception("export_crisis_photos failed export_id=%s", export_id)
        await job_row.safe_mark_failed(format_terminal_error(exc))
        raise
    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)


class _BundleWriter:
    """Independently openable ZIP_STORED parts; photos are already compressed."""

    def __init__(
        self,
        *,
        scratch_dir: str,
        crisis_id: uuid.UUID,
        export_id: uuid.UUID,
        store: ExportPartStore,
        part_size_bytes: int,
    ) -> None:
        self._scratch_dir = scratch_dir
        self._crisis_id = crisis_id
        self._export_id = export_id
        self._store = store
        self._part_size = part_size_bytes
        self.parts: list[dict[str, Any]] = []
        self._index = 0
        self._zf: zipfile.ZipFile | None = None
        self._path: str | None = None
        self._part_photos = 0

    def _ensure_open(self) -> zipfile.ZipFile:
        if self._zf is None:
            self._index += 1
            self._path = os.path.join(self._scratch_dir, f"part-{self._index:02d}.zip")
            # The size check runs after each write, so a part can pass 4 GB.
            self._zf = zipfile.ZipFile(self._path, "w", zipfile.ZIP_STORED, allowZip64=True)
            self._part_photos = 0
        return self._zf

    def write_bytes(self, name: str, data: bytes) -> None:
        self._ensure_open().writestr(name, data)

    @contextlib.contextmanager
    def open_entry(self, name: str) -> Generator[Any, None, None]:
        zf = self._ensure_open()
        with zf.open(name, "w") as dest:
            yield dest

    async def add_photo(self, name: str, data: bytes) -> None:
        self._ensure_open().writestr(name, data)
        self._part_photos += 1
        if self._path is not None and os.path.getsize(self._path) >= self._part_size:
            await self._flush()

    async def _flush(self) -> None:
        if self._zf is None or self._path is None:
            return
        self._zf.close()
        key = f"{self._crisis_id}/{self._export_id}/part-{self._index:02d}.zip"
        size = await self._store.upload_export_part(self._path, key)
        self.parts.append({"key": key, "bytes": size, "photo_count": self._part_photos})
        os.remove(self._path)
        self._zf = None
        self._path = None

    async def finish(self) -> None:
        await self._flush()

    @property
    def total_bytes(self) -> int:
        return sum(int(p["bytes"]) for p in self.parts)


async def _load_row(
    sessionmaker: async_sessionmaker[AsyncSession], export_id: uuid.UUID
) -> tuple[uuid.UUID, dict[str, Any], str, str, str | None] | None:
    async with sessionmaker() as session:
        row = (
            await session.execute(
                text(
                    "select crisis_id, filters, format, scope, created_by_email "
                    "from public.report_exports where id = :id"
                ),
                {"id": str(export_id)},
            )
        ).first()
    if row is None:
        return None
    filters: dict[str, Any] = row.filters or {}
    return row.crisis_id, filters, row.format, row.scope, row.created_by_email


async def _fetch_crisis_name(session: AsyncSession, crisis_id: uuid.UUID) -> str | None:
    row = (
        await session.execute(
            text("select name from public.crises where id = :id"),
            {"id": str(crisis_id)},
        )
    ).first()
    return row.name if row is not None else None


__all__ = ["export_crisis_photos", "run_export_crisis_photos"]
