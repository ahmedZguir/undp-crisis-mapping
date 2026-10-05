"""Report enrichment: a dispatcher plus translate and caption sub-jobs.

Every sub-job ends with an atomic finalize; the writer that wins it enqueues
the downstream embed and score jobs exactly once. Transient model errors are
retried by Arq; deterministic failures are written as 'failed' immediately.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.ai import (
    AIClientUnavailableError,
    detect_and_translate,
    score_relevance,
)
from api.ai import (
    caption_image as caption_image_primitive,
)
from api.ai.llm import LLMOutputError
from api.workers.context import (
    ai_clients_from_ctx,
    arq_pool_from_ctx,
    sessionmaker_from_ctx,
    storage_from_ctx,
)
from api.workers.errors import format_error, is_last_try
from api.workers.sidecars import execute_write, mark_failed, mark_skipped

logger = logging.getLogger(__name__)


# Closed set: each value is interpolated into SQL as a column name.
TranslatableField = Literal["description", "route_description"]
_TRANSLATABLE_FIELDS: tuple[TranslatableField, ...] = ("description", "route_description")

SUB_JOB_MAX_TRIES = 3


async def enrich_report(ctx: dict[str, object], report_id: str) -> None:
    """Enqueue sub-jobs for pending stages; stages with no input are marked 'skipped'."""
    rid = uuid.UUID(report_id)
    sessionmaker = sessionmaker_from_ctx(ctx)
    snapshot = await _load_dispatch_snapshot(sessionmaker, rid)
    if snapshot is None:
        logger.warning("enrich_report.report_missing report_id=%s", rid)
        return

    pool = arq_pool_from_ctx(ctx)
    something_changed = False

    for field in _TRANSLATABLE_FIELDS:
        status = snapshot.translation_status(field)
        raw = snapshot.field_text(field)
        if status != "pending":
            continue
        if not raw:
            await _mark_translation_skipped(sessionmaker, rid, field)
            something_changed = True
            continue
        await pool.enqueue_job("translate_field", report_id, field)

    if snapshot.caption_status == "pending":
        if snapshot.has_photo:
            await pool.enqueue_job("caption_image", report_id)
        else:
            await _mark_caption_skipped(sessionmaker, rid)
            something_changed = True

    # Geocoding is outside the finalize gate.
    if snapshot.geocode_status == "pending":
        if snapshot.needs_geocode():
            await pool.enqueue_job("geocode_report", report_id)
        else:
            await _mark_geocode_skipped(sessionmaker, rid)

    if something_changed:
        # Skips alone may complete the report, and no sub-job would finalize it.
        await _try_finalize(ctx, sessionmaker, rid)


async def translate_field(ctx: dict[str, object], report_id: str, field: str) -> None:
    if field not in _TRANSLATABLE_FIELDS:
        raise ValueError(f"unknown translatable field: {field!r}")
    typed_field: TranslatableField = field  # pyright narrows via the `in` check
    rid = uuid.UUID(report_id)
    sessionmaker = sessionmaker_from_ctx(ctx)
    clients = ai_clients_from_ctx(ctx)

    try:
        raw_text = await _load_field_text(sessionmaker, rid, typed_field)
    except _ReportMissingError:
        logger.warning("translate_field.report_missing report_id=%s", rid)
        return

    if not raw_text:
        await _mark_translation_skipped(sessionmaker, rid, typed_field)
        await _try_finalize(ctx, sessionmaker, rid)
        return

    try:
        result = await detect_and_translate(raw_text, clients=clients)
    except AIClientUnavailableError as exc:
        await _write_translation_failed(sessionmaker, rid, typed_field, str(exc))
        await _try_finalize(ctx, sessionmaker, rid)
        return
    except LLMOutputError as exc:
        await _write_translation_failed(sessionmaker, rid, typed_field, f"parse_error: {exc}")
        await _try_finalize(ctx, sessionmaker, rid)
        return
    except Exception as exc:
        if is_last_try(ctx):
            await _write_translation_failed(sessionmaker, rid, typed_field, format_error(exc))
            await _try_finalize(ctx, sessionmaker, rid)
        raise

    if result.lang == "en":
        lang_out, text_en_out, status = "en", raw_text, "ready"
    elif result.lang == "und":
        lang_out, text_en_out, status = "und", raw_text, "passthrough"
    else:
        lang_out, text_en_out, status = result.lang, result.text_en, "ready"

    await _write_translation_ok(
        sessionmaker, rid, typed_field, lang=lang_out, text_en=text_en_out, status=status
    )
    await _try_finalize(ctx, sessionmaker, rid)


async def caption_image(ctx: dict[str, object], report_id: str) -> None:
    """Caption the photo and score its relevance in one job, since relevance reads the caption."""
    rid = uuid.UUID(report_id)
    sessionmaker = sessionmaker_from_ctx(ctx)
    clients = ai_clients_from_ctx(ctx)
    downloader = storage_from_ctx(ctx)

    try:
        photo_path = await _load_photo_path(sessionmaker, rid)
    except _ReportMissingError:
        logger.warning("caption_image.report_missing report_id=%s", rid)
        return

    try:
        photo_bytes, _ = await downloader.download_photo(photo_path)
    except Exception as exc:
        # Treated as transient; a permanently missing blob is rare.
        if is_last_try(ctx):
            await _write_caption_failed(sessionmaker, rid, f"download_failed: {format_error(exc)}")
            await _try_finalize(ctx, sessionmaker, rid)
        raise

    try:
        caption_result = await caption_image_primitive(photo_bytes, clients=clients)
    except AIClientUnavailableError as exc:
        await _write_caption_failed(sessionmaker, rid, str(exc))
        await _try_finalize(ctx, sessionmaker, rid)
        return
    except Exception as exc:
        if is_last_try(ctx):
            await _write_caption_failed(sessionmaker, rid, format_error(exc))
            await _try_finalize(ctx, sessionmaker, rid)
        raise

    if not caption_result.caption:
        await _write_caption_failed(sessionmaker, rid, "empty_caption")
        await _try_finalize(ctx, sessionmaker, rid)
        return

    try:
        relevance = await score_relevance(caption_result.caption, clients=clients)
    except AIClientUnavailableError as exc:
        await _write_caption_failed(sessionmaker, rid, str(exc))
        await _try_finalize(ctx, sessionmaker, rid)
        return
    except LLMOutputError as exc:
        await _write_caption_failed(sessionmaker, rid, f"relevance_parse_error: {exc}")
        await _try_finalize(ctx, sessionmaker, rid)
        return
    except Exception as exc:
        if is_last_try(ctx):
            await _write_caption_failed(sessionmaker, rid, f"relevance: {format_error(exc)}")
            await _try_finalize(ctx, sessionmaker, rid)
        raise

    await _write_caption_ok(
        sessionmaker,
        rid,
        caption=caption_result.caption,
        label=relevance.label,
        score=relevance.score,
    )
    await _try_finalize(ctx, sessionmaker, rid)


class _ReportMissingError(LookupError):
    """The report was deleted before the job ran."""


@dataclass(frozen=True, slots=True)
class _DispatchSnapshot:
    description: str | None
    description_status: str
    route_description: str | None
    route_description_status: str
    caption_status: str
    # None when the report has no geocode sidecar row.
    geocode_status: str | None
    has_location: bool
    has_building: bool
    has_photo: bool

    def translation_status(self, field: TranslatableField) -> str:
        if field == "description":
            return self.description_status
        return self.route_description_status

    def field_text(self, field: TranslatableField) -> str | None:
        if field == "description":
            return self.description
        return self.route_description

    def needs_geocode(self) -> bool:
        """Geocode only reports with route text and neither a GPS point nor a building."""
        if self.has_location or self.has_building:
            return False
        return bool(self.route_description and self.route_description.strip())


async def _load_dispatch_snapshot(
    sessionmaker: async_sessionmaker[AsyncSession], report_id: uuid.UUID
) -> _DispatchSnapshot | None:
    async with sessionmaker() as session:
        row = (
            await session.execute(
                text(
                    "select r.description, r.route_description, "
                    "       (r.location is not null) as has_location, "
                    "       (r.building_id is not null) as has_building, "
                    "       (r.photo_path is not null) as has_photo, "
                    "       t.description_status, t.route_description_status, "
                    "       c.status as caption_status, "
                    "       g.status as geocode_status "
                    "  from public.reports r "
                    "  join public.report_translations t on t.report_id = r.id "
                    "  join public.image_captions c on c.report_id = r.id "
                    "  left join public.report_geocodes g on g.report_id = r.id "
                    " where r.id = :id"
                ),
                {"id": str(report_id)},
            )
        ).first()
    if row is None:
        return None
    return _DispatchSnapshot(
        description=row.description,
        description_status=row.description_status,
        route_description=row.route_description,
        route_description_status=row.route_description_status,
        caption_status=row.caption_status,
        geocode_status=row.geocode_status,
        has_location=row.has_location,
        has_building=row.has_building,
        has_photo=row.has_photo,
    )


async def _load_field_text(
    sessionmaker: async_sessionmaker[AsyncSession],
    report_id: uuid.UUID,
    field: TranslatableField,
) -> str | None:
    column = field  # closed enum, safe to interpolate
    async with sessionmaker() as session:
        row = (
            await session.execute(
                text(f"select {column} as value from public.reports where id = :id"),
                {"id": str(report_id)},
            )
        ).first()
    if row is None:
        raise _ReportMissingError(str(report_id))
    return row.value


async def _load_photo_path(
    sessionmaker: async_sessionmaker[AsyncSession], report_id: uuid.UUID
) -> str:
    async with sessionmaker() as session:
        row = (
            await session.execute(
                text("select photo_path from public.reports where id = :id"),
                {"id": str(report_id)},
            )
        ).first()
    if row is None:
        raise _ReportMissingError(str(report_id))
    return row.photo_path


async def _mark_translation_skipped(
    sessionmaker: async_sessionmaker[AsyncSession],
    report_id: uuid.UUID,
    field: TranslatableField,
) -> None:
    await mark_skipped(
        sessionmaker,
        report_id,
        table="report_translations",
        status_col=f"{field}_status",
        error_col=f"{field}_error",
    )


async def _mark_geocode_skipped(
    sessionmaker: async_sessionmaker[AsyncSession],
    report_id: uuid.UUID,
) -> None:
    await mark_skipped(sessionmaker, report_id, table="report_geocodes")


async def _mark_caption_skipped(
    sessionmaker: async_sessionmaker[AsyncSession],
    report_id: uuid.UUID,
) -> None:
    await mark_skipped(sessionmaker, report_id, table="image_captions")


async def _write_translation_ok(
    sessionmaker: async_sessionmaker[AsyncSession],
    report_id: uuid.UUID,
    field: TranslatableField,
    *,
    lang: str,
    text_en: str,
    status: str,
) -> None:
    await execute_write(
        sessionmaker,
        f"update public.report_translations "
        f"   set {field}_lang = :lang, "
        f"       {field}_en = :text_en, "
        f"       {field}_status = :status, "
        f"       {field}_error = null, "
        f"       updated_at = now() "
        f" where report_id = :id",
        {"id": str(report_id), "lang": lang, "text_en": text_en, "status": status},
    )


async def _write_translation_failed(
    sessionmaker: async_sessionmaker[AsyncSession],
    report_id: uuid.UUID,
    field: TranslatableField,
    error: str,
) -> None:
    await mark_failed(
        sessionmaker,
        report_id,
        error,
        table="report_translations",
        status_col=f"{field}_status",
        error_col=f"{field}_error",
    )


async def _write_caption_ok(
    sessionmaker: async_sessionmaker[AsyncSession],
    report_id: uuid.UUID,
    *,
    caption: str,
    label: str,
    score: float,
) -> None:
    await execute_write(
        sessionmaker,
        "update public.image_captions "
        "   set caption = :caption, "
        "       relevance_label = :label, "
        "       relevance_score = :score, "
        "       status = 'ready', "
        "       error = null, "
        "       updated_at = now() "
        " where report_id = :id",
        {"id": str(report_id), "caption": caption, "label": label, "score": score},
    )


async def _write_caption_failed(
    sessionmaker: async_sessionmaker[AsyncSession],
    report_id: uuid.UUID,
    error: str,
) -> None:
    await mark_failed(sessionmaker, report_id, error, table="image_captions")


_FINALIZE_SQL = text(
    """
    update public.reports r
       set enrichment_finalized_at = now()
      from public.report_translations t,
           public.image_captions c
     where r.id = :id
       and r.id = t.report_id
       and r.id = c.report_id
       and r.enrichment_finalized_at is null
       and t.description_status in ('ready','passthrough','skipped','failed')
       and t.route_description_status in ('ready','passthrough','skipped','failed')
       and c.status in ('ready','skipped','failed')
    returning r.id
    """
)


async def _try_finalize(
    ctx: dict[str, object],
    sessionmaker: async_sessionmaker[AsyncSession],
    report_id: uuid.UUID,
) -> None:
    """Finalize if every stage is terminal; only the winning writer enqueues downstream jobs."""
    async with sessionmaker() as session, session.begin():
        row = (await session.execute(_FINALIZE_SQL, {"id": str(report_id)})).first()
    if row is None:
        return
    pool = arq_pool_from_ctx(ctx)
    await pool.enqueue_job("embed_report", str(report_id))
    await pool.enqueue_job("score_report", str(report_id))
