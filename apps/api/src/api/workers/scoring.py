"""Reporter points, badges and confidence score for a finalized report.

Enqueued once, by the enrichment finalize. Confidence is stored here and only
re-derived later by the coordinator verify endpoint.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.ai import AIClientUnavailableError, classify_damage
from api.reports.quality import (
    QUALITY_POINTS_BAR,
    Quality,
    compute_points,
    confidence_from_quality,
)
from api.workers.context import ai_clients_from_ctx, sessionmaker_from_ctx, storage_from_ctx

logger = logging.getLogger(__name__)


# The skew tolerance absorbs device clock drift.
PHOTO_FRESH_MAX_AGE = timedelta(days=7)
PHOTO_FRESH_SKEW = timedelta(days=1)

GPS_MATCH_THRESHOLD_M = 100.0

GROUND_TRUTH_MIN = 5  # reports with points >= QUALITY_POINTS_BAR
AREA_MAPPER_MIN = 5  # distinct building_ids
ACTIVE_RESPONDER_MIN = 3  # quality reports within a 24h window
COMMUNITY_ANCHOR_MIN = 3  # corroborators_500m on a single report
ACTIVE_RESPONDER_WINDOW = timedelta(hours=24)


async def score_report(ctx: dict[str, object], report_id: str) -> None:
    rid = uuid.UUID(report_id)
    sessionmaker = sessionmaker_from_ctx(ctx)

    row = await _load_report(sessionmaker, rid)
    if row is None:
        logger.warning("score_report.report_missing report_id=%s", rid)
        return

    has_photo = row.photo_path is not None
    has_description = bool(row.description and row.description.strip())

    is_duplicate_image = False
    if has_photo:
        is_duplicate_image = await _is_duplicate_image(sessionmaker, row.photo_path, rid)

    damage_agreement: bool | None = None
    if has_photo and not is_duplicate_image:
        damage_agreement = await _damage_agreement(ctx, row.photo_path, row.damage_class)

    photo_fresh = _photo_fresh(row.photo_captured_at, row.created_at)
    gps_pin_match = (
        (row.exif_gps_distance_m <= GPS_MATCH_THRESHOLD_M)
        if row.exif_gps_distance_m is not None
        else None
    )
    corroborators = await _count_corroborators(sessionmaker, rid)
    reputation = await _reporter_reputation(sessionmaker, row.client_id, rid)

    quality = Quality(
        has_photo=has_photo,
        has_description=has_description,
        relevance_label=row.relevance_label,
        damage_agreement=damage_agreement,
        photo_fresh=photo_fresh,
        gps_pin_match=gps_pin_match,
        corroborators_500m=corroborators,
        reporter_reputation=reputation,
        is_duplicate_image=is_duplicate_image,
    )
    points = compute_points(quality)
    confidence = confidence_from_quality(quality, verified=False)

    await _write_quality(sessionmaker, rid, quality, points=points, confidence=confidence)

    # Duplicates earn no badge progress.
    if not is_duplicate_image and row.client_id is not None:
        await _award_badges(
            sessionmaker,
            client_id=row.client_id,
            report_id=rid,
            created_at=row.created_at,
            corroborators=corroborators,
        )


def _photo_fresh(captured_at: datetime | None, created_at: datetime) -> bool | None:
    if captured_at is None:
        return None
    delta = created_at - captured_at
    return -PHOTO_FRESH_SKEW <= delta <= PHOTO_FRESH_MAX_AGE


async def _damage_agreement(
    ctx: dict[str, object],
    photo_path: str,
    user_class: str,
) -> bool | None:
    """Best-effort: any failure returns None and never fails the job.

    Re-downloads the photo that caption_image already fetched.
    """
    try:
        downloader = storage_from_ctx(ctx)
        clients = ai_clients_from_ctx(ctx)
        photo_bytes, content_type = await downloader.download_photo(photo_path)
        predicted = await classify_damage(photo_bytes, content_type=content_type, clients=clients)
    except AIClientUnavailableError:
        logger.info("score_report.classifier_unavailable photo=%s", photo_path)
        return None
    except Exception:
        logger.warning("score_report.damage_classify_failed photo=%s", photo_path, exc_info=True)
        return None
    return predicted == user_class


_LOAD_REPORT_SQL = text(
    """
    select
        r.client_id,
        r.damage_class,
        r.description,
        r.photo_path,
        r.created_at,
        r.photo_captured_at,
        case
            when r.photo_exif_gps is not null and r.location is not null
            then st_distance(r.photo_exif_gps, r.location)
        end as exif_gps_distance_m,
        c.relevance_label
    from public.reports r
    join public.image_captions c on c.report_id = r.id
    where r.id = :id
    """
)


async def _load_report(
    sessionmaker: async_sessionmaker[AsyncSession], report_id: uuid.UUID
) -> Any | None:
    async with sessionmaker() as session:
        return (await session.execute(_LOAD_REPORT_SQL, {"id": str(report_id)})).first()


# Distinct other reporters within 500 m in the same crisis (geography, so metres).
_CORROBORATORS_SQL = text(
    """
    select count(distinct nr.client_id) as n
    from public.reports me
    join public.reports nr
      on nr.crisis_id = me.crisis_id
     and nr.id <> me.id
     and nr.map_point is not null
     and st_dwithin(nr.map_point, me.map_point, 500)
    where me.id = :id
      and me.map_point is not null
      and nr.client_id is not null
      and (me.client_id is null or nr.client_id is distinct from me.client_id)
    """
)


async def _count_corroborators(
    sessionmaker: async_sessionmaker[AsyncSession], report_id: uuid.UUID
) -> int:
    async with sessionmaker() as session:
        row = (await session.execute(_CORROBORATORS_SQL, {"id": str(report_id)})).first()
    return int(row.n) if row and row.n is not None else 0


_REPUTATION_SQL = text(
    """
    select count(*) as n
    from public.report_quality
    where client_id = :client_id
      and verified = true
      and report_id <> :id
    """
)


async def _reporter_reputation(
    sessionmaker: async_sessionmaker[AsyncSession],
    client_id: uuid.UUID | None,
    report_id: uuid.UUID,
) -> int:
    if client_id is None:
        return 0
    async with sessionmaker() as session:
        row = (
            await session.execute(
                _REPUTATION_SQL, {"client_id": str(client_id), "id": str(report_id)}
            )
        ).first()
    return int(row.n) if row and row.n is not None else 0


_DUPLICATE_IMAGE_SQL = text(
    """
    select exists(
        select 1 from public.reports o
        where o.photo_path = :photo_path and o.id <> :id
    ) as dup
    """
)


async def _is_duplicate_image(
    sessionmaker: async_sessionmaker[AsyncSession],
    photo_path: str,
    report_id: uuid.UUID,
) -> bool:
    """True when another report uses the same image.

    Photo paths are content-addressed, so identical bytes share a path.
    """
    async with sessionmaker() as session:
        row = (
            await session.execute(
                _DUPLICATE_IMAGE_SQL, {"photo_path": photo_path, "id": str(report_id)}
            )
        ).first()
    return bool(row.dup) if row else False


_WRITE_QUALITY_SQL = text(
    """
    update public.report_quality
       set has_photo = :has_photo,
           has_description = :has_description,
           relevance_label = :relevance_label,
           damage_agreement = :damage_agreement,
           points = :points,
           photo_fresh = :photo_fresh,
           gps_pin_match = :gps_pin_match,
           corroborators_500m = :corroborators,
           reporter_reputation = :reputation,
           confidence_score = :confidence,
           is_duplicate_image = :is_duplicate_image,
           computed_at = now()
     where report_id = :id
    """
)


async def _write_quality(
    sessionmaker: async_sessionmaker[AsyncSession],
    report_id: uuid.UUID,
    q: Quality,
    *,
    points: int,
    confidence: float,
) -> None:
    async with sessionmaker() as session, session.begin():
        await session.execute(
            _WRITE_QUALITY_SQL,
            {
                "id": str(report_id),
                "has_photo": q.has_photo,
                "has_description": q.has_description,
                "relevance_label": q.relevance_label,
                "damage_agreement": q.damage_agreement,
                "points": points,
                "photo_fresh": q.photo_fresh,
                "gps_pin_match": q.gps_pin_match,
                "corroborators": q.corroborators_500m,
                "reputation": q.reporter_reputation,
                "confidence": confidence,
                "is_duplicate_image": q.is_duplicate_image,
            },
        )


# Counts include the report_quality row just written for this report.
_BADGE_COUNTS_SQL = text(
    """
    select
        (select count(*) from public.report_quality
           where client_id = :client_id and points >= :quality_bar) as quality_reports,
        (select count(distinct r.building_id) from public.reports r
           where r.client_id = :client_id and r.building_id is not null) as distinct_buildings,
        (select count(*) from public.report_quality rq
           join public.reports r on r.id = rq.report_id
          where rq.client_id = :client_id
            and rq.points >= :quality_bar
            and r.created_at >= :window_start) as recent_quality_reports
    """
)

_INSERT_BADGE_SQL = text(
    """
    insert into public.citizen_badges (client_id, badge_slug, report_id)
    values (:client_id, :badge_slug, :report_id)
    on conflict (client_id, badge_slug) do nothing
    """
)


async def _award_badges(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    client_id: uuid.UUID,
    report_id: uuid.UUID,
    created_at: datetime,
    corroborators: int,
) -> None:
    """Insert newly earned badges. `verified_by_coordinator` is awarded by the verify endpoint."""
    window_start = created_at - ACTIVE_RESPONDER_WINDOW
    async with sessionmaker() as session, session.begin():
        counts = (
            await session.execute(
                _BADGE_COUNTS_SQL,
                {
                    "client_id": str(client_id),
                    "quality_bar": QUALITY_POINTS_BAR,
                    "window_start": window_start,
                },
            )
        ).one()

        earned: list[str] = ["first_report"]
        if counts.quality_reports >= GROUND_TRUTH_MIN:
            earned.append("ground_truth")
        if counts.distinct_buildings >= AREA_MAPPER_MIN:
            earned.append("area_mapper")
        if counts.recent_quality_reports >= ACTIVE_RESPONDER_MIN:
            earned.append("active_responder")
        if corroborators >= COMMUNITY_ANCHOR_MIN:
            earned.append("community_anchor")

        for slug in earned:
            await session.execute(
                _INSERT_BADGE_SQL,
                {"client_id": str(client_id), "badge_slug": slug, "report_id": str(report_id)},
            )
