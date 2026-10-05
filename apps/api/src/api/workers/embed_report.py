from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.ai import (
    EMBEDDING_VERSION,
    QWEN3_EMBEDDING_DIM,
    AIClientUnavailableError,
    embed_passage,
    halfvec,
)
from api.ai.report_text import report_detail_lines
from api.workers.context import ai_clients_from_ctx, sessionmaker_from_ctx

logger = logging.getLogger(__name__)


async def embed_report(ctx: dict[str, object], report_id: str) -> None:
    """Embed a finalised report. Insert-only; the report_id primary key makes re-runs no-ops."""
    rid = uuid.UUID(report_id)
    sessionmaker = sessionmaker_from_ctx(ctx)
    clients = ai_clients_from_ctx(ctx)

    snapshot = await _load_snapshot(sessionmaker, rid)
    if snapshot is None:
        logger.warning("embed_report.report_missing report_id=%s", rid)
        return

    if await _embedding_exists(sessionmaker, rid):
        return

    document = _assemble_document(snapshot)
    if not document:
        logger.info("embed_report.no_text report_id=%s", rid)
        return

    try:
        result = await embed_passage(document, client=clients.embedding)
    except AIClientUnavailableError as exc:
        logger.warning("embed_report.client_unavailable report_id=%s: %s", rid, exc)
        return

    if result.dimension != QWEN3_EMBEDDING_DIM:
        # The endpoint is serving a different model; surface it instead of failing the insert.
        logger.error(
            "embed_report.dim_mismatch report_id=%s expected=%d got=%d model=%s",
            rid,
            QWEN3_EMBEDDING_DIM,
            result.dimension,
            result.model,
        )
        return

    await _insert_embedding(
        sessionmaker,
        rid,
        vector=result.vector,
        model=result.model,
        dimension=result.dimension,
    )


@dataclass(frozen=True, slots=True)
class _Snapshot:
    description_en: str | None
    caption: str | None
    building_name: str | None
    infra_type: list[str] | None
    damage_class: str | None
    debris: bool | None


_SNAPSHOT_SQL = text(
    """
    select
        t.description_en,
        c.caption,
        b.name as building_name,
        r.infra_type,
        r.damage_class,
        r.debris
    from public.reports r
    left join public.report_translations t on t.report_id = r.id
    left join public.image_captions c on c.report_id = r.id
    left join public.buildings b on b.id = r.building_id
    where r.id = :id
    """
)


_EXISTS_SQL = text("select 1 from public.report_embeddings where report_id = :id")


_INSERT_SQL = text(
    """
    insert into public.report_embeddings
        (report_id, embedding, model, dimension, embedding_version)
    values
        (:id, cast(:embedding as halfvec), :model, :dimension, :version)
    on conflict (report_id) do nothing
    """
)


async def _load_snapshot(
    sessionmaker: async_sessionmaker[AsyncSession], report_id: uuid.UUID
) -> _Snapshot | None:
    async with sessionmaker() as session:
        row = (await session.execute(_SNAPSHOT_SQL, {"id": str(report_id)})).first()
    if row is None:
        return None
    return _Snapshot(
        description_en=row.description_en,
        caption=row.caption,
        building_name=row.building_name,
        infra_type=list(row.infra_type) if row.infra_type is not None else None,
        damage_class=row.damage_class,
        debris=row.debris,
    )


async def _embedding_exists(
    sessionmaker: async_sessionmaker[AsyncSession], report_id: uuid.UUID
) -> bool:
    async with sessionmaker() as session:
        row = (await session.execute(_EXISTS_SQL, {"id": str(report_id)})).first()
    return row is not None


async def _insert_embedding(
    sessionmaker: async_sessionmaker[AsyncSession],
    report_id: uuid.UUID,
    *,
    vector: list[float],
    model: str,
    dimension: int,
) -> None:
    payload = halfvec.literal(vector)
    async with sessionmaker() as session, session.begin():
        await session.execute(
            _INSERT_SQL,
            {
                "id": str(report_id),
                "embedding": payload,
                "model": model,
                "dimension": dimension,
                "version": EMBEDDING_VERSION,
            },
        )


def _assemble_document(snap: _Snapshot) -> str:
    """Description and caption, then `Key: value` lines. Empty if there is no free text."""
    if not snap.description_en and not snap.caption:
        return ""
    return "\n".join(report_detail_lines(asdict(snap)))


__all__ = ["embed_report"]
