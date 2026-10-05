"""SQL and row projectors for the admin reports routes."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.admin.report_sql import (
    GEOCODE_JOIN,
    GEOCODE_PROJECTION,
    LOCATION_SOURCE_SQL,
    float_or_none,
    geocode_meta,
)
from api.core.listing import Bbox, decode_cursor, encode_cursor
from api.reports.badges import VERIFIED_BY_COORDINATOR
from api.reports.quality import QUALITY_POINTS_BAR, Quality, confidence_from_quality
from api.schemas import as_damage_class, as_debris
from api.schemas.admin_reports import (
    AdminReportDetailResponse,
    AdminReportListItem,
    AdminReportListResponse,
    ReporterReputationOut,
    ReportQualityOut,
)
from api.schemas.common import location_or_none
from api.schemas.reports import PhotoCamera, PhotoGps, PhotoMetadata

# reports.map_point is the materialised coalesce of pin, building centroid and
# geocode point, GiST-indexed for the bbox predicate. The geocode join only
# supplies the uncertainty metadata the admin map renders.
_MAP_GEOG = "r.map_point"

# Left join: older reports have no report_quality row.
_QUALITY_JOIN = "left join public.report_quality rq on rq.report_id = r.id"
_QUALITY_LIST_PROJECTION = "rq.confidence_score, coalesce(rq.verified, false) as q_verified"
_QUALITY_DETAIL_PROJECTION = """
        rq.confidence_score,
        rq.points as quality_points,
        rq.relevance_label as quality_relevance_label,
        rq.damage_agreement,
        rq.photo_fresh,
        rq.gps_pin_match,
        rq.corroborators_500m,
        rq.reporter_reputation,
        coalesce(rq.verified, false) as q_verified,
        rq.verified_at,
        coalesce(rq.is_duplicate_image, false) as is_duplicate_image,
        rq.computed_at as quality_computed_at,
"""


_LIST_SQL = text(
    f"""
    select
        r.id, r.crisis_id, r.damage_class, r.description, r.route_description,
        r.infra_type, r.infra_name,
        r.crisis_type, r.crisis_type_detailed, r.debris,
        r.building_id, r.client_id, r.client_submission_id,
        r.photo_path, r.created_at,
        st_y(r.location::geometry) as report_lat,
        st_x(r.location::geometry) as report_lng,
        st_y(b.centroid::geometry) as building_lat,
        st_x(b.centroid::geometry) as building_lng,
        {GEOCODE_PROJECTION}
        {LOCATION_SOURCE_SQL} as location_source,
        {_QUALITY_LIST_PROJECTION}
    from public.reports r
    left join public.buildings b on b.id = r.building_id
    {GEOCODE_JOIN}
    {_QUALITY_JOIN}
    where r.crisis_id = :crisis_id
      and (cast(:damage_class as text) is null or r.damage_class = :damage_class)
      and (
          cast(:cursor_ts as timestamptz) is null
          or (r.created_at, r.id) < (cast(:cursor_ts as timestamptz), cast(:cursor_id as uuid))
      )
    order by r.created_at desc, r.id desc
    limit :fetch_limit
    """
)


_BBOX_SQL = text(
    f"""
    with candidates as (
        select
            r.id, r.crisis_id, r.damage_class, r.description, r.route_description,
            r.infra_type, r.infra_name,
            r.crisis_type, r.crisis_type_detailed, r.debris,
            r.building_id, r.client_id, r.client_submission_id,
            r.photo_path, r.created_at,
            st_y(r.location::geometry) as report_lat,
            st_x(r.location::geometry) as report_lng,
            st_y(b.centroid::geometry) as building_lat,
            st_x(b.centroid::geometry) as building_lng,
            {GEOCODE_PROJECTION}
            {LOCATION_SOURCE_SQL} as location_source,
            {_QUALITY_LIST_PROJECTION},
            {_MAP_GEOG} as map_geog
        from public.reports r
        left join public.buildings b on b.id = r.building_id
        {GEOCODE_JOIN}
        {_QUALITY_JOIN}
        where r.crisis_id = :crisis_id
          and (cast(:damage_class as text) is null or r.damage_class = :damage_class)
          and {_MAP_GEOG} is not null
          and st_intersects(
              {_MAP_GEOG},
              st_makeenvelope(:west, :south, :east, :north, 4326)::geography
          )
    )
    select *, count(*) over () as total_in_bbox
    from candidates
    order by created_at desc, id desc
    limit :limit
    """
)


_DETAIL_SQL = text(
    f"""
    select
        r.id, r.crisis_id, r.damage_class, r.description, r.route_description,
        r.infra_type, r.infra_name,
        r.crisis_type, r.crisis_type_detailed, r.debris,
        r.building_id, r.client_id, r.client_submission_id,
        b.name as building_name,
        r.photo_path, r.created_at,
        st_y(r.location::geometry) as report_lat,
        st_x(r.location::geometry) as report_lng,
        st_y(b.centroid::geometry) as building_lat,
        st_x(b.centroid::geometry) as building_lng,
        r.photo_captured_at,
        r.photo_exif_extracted_at,
        st_y(r.photo_exif_gps::geometry) as photo_exif_lat,
        st_x(r.photo_exif_gps::geometry) as photo_exif_lng,
        r.photo_exif_meta,
        t.description_lang,
        t.description_en,
        t.description_status,
        t.route_description_lang,
        t.route_description_en,
        t.route_description_status,
        ic.caption as ai_caption,
        ic.status as ai_caption_status,
        {_QUALITY_DETAIL_PROJECTION}
        {LOCATION_SOURCE_SQL} as location_source
    from public.reports r
    left join public.buildings b on b.id = r.building_id
    {GEOCODE_JOIN}
    {_QUALITY_JOIN}
    left join public.report_translations t on t.report_id = r.id
    left join public.image_captions ic on ic.report_id = r.id
    where r.id = :report_id
    """
)


async def ensure_crisis_exists(session: AsyncSession, crisis_id: uuid.UUID) -> bool:
    exists = (
        await session.execute(
            text("select 1 from public.crises where id = :id"),
            {"id": str(crisis_id)},
        )
    ).first()
    return exists is not None


async def list_by_cursor(
    session: AsyncSession,
    *,
    crisis_id: uuid.UUID,
    cursor: str | None,
    damage_class: str | None,
    limit: int,
) -> AdminReportListResponse:
    cursor_ts: datetime | None = None
    cursor_id: uuid.UUID | None = None
    if cursor is not None:
        cursor_ts, cursor_id = decode_cursor(cursor)

    # Fetch one extra row to detect a next page without a second count query.
    fetch_limit = limit + 1
    result = await session.execute(
        _LIST_SQL,
        {
            "crisis_id": str(crisis_id),
            "damage_class": damage_class,
            "cursor_ts": cursor_ts,
            "cursor_id": str(cursor_id) if cursor_id is not None else None,
            "fetch_limit": fetch_limit,
        },
    )
    rows = result.all()

    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = encode_cursor(page[-1].created_at, page[-1].id) if has_more and page else None

    return AdminReportListResponse(
        items=[row_to_list_item(r) for r in page],
        next_cursor=next_cursor,
        truncated=False,
        total_in_bbox=None,
    )


async def list_by_bbox(
    session: AsyncSession,
    *,
    crisis_id: uuid.UUID,
    bbox: Bbox,
    damage_class: str | None,
    limit: int,
) -> AdminReportListResponse:
    result = await session.execute(
        _BBOX_SQL,
        {
            "crisis_id": str(crisis_id),
            "damage_class": damage_class,
            "west": bbox.west,
            "south": bbox.south,
            "east": bbox.east,
            "north": bbox.north,
            "limit": limit,
        },
    )
    rows = result.all()

    total = int(rows[0].total_in_bbox) if rows else 0
    return AdminReportListResponse(
        items=[row_to_list_item(r) for r in rows],
        next_cursor=None,
        truncated=total > limit,
        total_in_bbox=total,
    )


# Verify
#
# Older reports have no report_quality row; create one from the report's own
# columns so any report can be verified. Signals stay at their defaults since
# the verified floor dominates the score.
_ENSURE_QUALITY_ROW_SQL = text(
    """
    insert into public.report_quality (report_id, client_id, has_photo, has_description)
    select r.id, r.client_id, (r.photo_path is not null),
           (r.description is not null and length(trim(r.description)) > 0)
    from public.reports r
    where r.id = :id
    on conflict (report_id) do nothing
    """
)

_SET_VERIFY_SQL = text(
    """
    update public.report_quality
       set verified = :verified,
           verified_by = :coordinator_id,
           verified_at = case when :verified then now() else null end
     where report_id = :id
    returning client_id, has_photo, has_description, relevance_label, damage_agreement,
              photo_fresh, gps_pin_match, corroborators_500m, reporter_reputation,
              coalesce(is_duplicate_image, false) as is_duplicate_image
    """
)

_SET_CONFIDENCE_SQL = text(
    "update public.report_quality set confidence_score = :score where report_id = :id"
)

_INSERT_VERIFIED_BADGE_SQL = text(
    """
    insert into public.citizen_badges (client_id, badge_slug, report_id)
    values (:client_id, :badge_slug, :report_id)
    on conflict (client_id, badge_slug) do nothing
    """
)

# Keep the badge while the client has any other verified report.
_REVOKE_VERIFIED_BADGE_SQL = text(
    """
    delete from public.citizen_badges
     where client_id = :client_id
       and badge_slug = :badge_slug
       and not exists (
           select 1 from public.report_quality
            where client_id = :client_id and verified = true
       )
    """
)


_REPUTATION_SQL = text(
    """
    select
        count(*) as total_reports,
        count(*) filter (where points >= :bar) as quality_reports,
        count(*) filter (where verified) as verified_count
    from public.report_quality
    where client_id = :client_id
    """
)
_REPUTATION_BADGES_SQL = text(
    "select badge_slug from public.citizen_badges where client_id = :client_id order by badge_slug"
)


async def fetch_reporter_reputation(
    session: AsyncSession, client_id: uuid.UUID | None
) -> ReporterReputationOut | None:
    """Submitter track record for the inspector; counts only reports with a quality row."""
    if client_id is None:
        return None
    totals = (
        await session.execute(
            _REPUTATION_SQL, {"client_id": str(client_id), "bar": QUALITY_POINTS_BAR}
        )
    ).one()
    slugs = list(
        (await session.execute(_REPUTATION_BADGES_SQL, {"client_id": str(client_id)})).scalars()
    )
    return ReporterReputationOut(
        total_reports=int(totals.total_reports),
        quality_reports=int(totals.quality_reports),
        verified_count=int(totals.verified_count),
        badge_count=len(slugs),
        badge_slugs=slugs,
    )


async def verify_report(
    session: AsyncSession,
    *,
    report_id: uuid.UUID,
    verified: bool,
    coordinator_id: uuid.UUID,
) -> tuple[float, bool, ReporterReputationOut | None] | None:
    """Set verify state, recompute confidence from the stored signals, sync the badge.

    Returns None if the report does not exist. Reputation is read after commit.
    """
    async with session.begin():
        await session.execute(_ENSURE_QUALITY_ROW_SQL, {"id": str(report_id)})
        row = (
            await session.execute(
                _SET_VERIFY_SQL,
                {
                    "id": str(report_id),
                    "verified": verified,
                    "coordinator_id": str(coordinator_id) if verified else None,
                },
            )
        ).first()
        if row is None:
            # No quality row even after the upsert, so the report does not exist.
            return None

        quality = Quality(
            has_photo=row.has_photo,
            has_description=row.has_description,
            relevance_label=row.relevance_label,
            damage_agreement=row.damage_agreement,
            photo_fresh=row.photo_fresh,
            gps_pin_match=row.gps_pin_match,
            corroborators_500m=row.corroborators_500m,
            reporter_reputation=row.reporter_reputation,
            is_duplicate_image=row.is_duplicate_image,
        )
        score = confidence_from_quality(quality, verified=verified)
        await session.execute(_SET_CONFIDENCE_SQL, {"id": str(report_id), "score": score})

        if row.client_id is not None:
            badge_params = {
                "client_id": str(row.client_id),
                "badge_slug": VERIFIED_BY_COORDINATOR,
                "report_id": str(report_id),
            }
            if verified:
                await session.execute(_INSERT_VERIFIED_BADGE_SQL, badge_params)
            else:
                await session.execute(
                    _REVOKE_VERIFIED_BADGE_SQL,
                    {"client_id": str(row.client_id), "badge_slug": VERIFIED_BY_COORDINATOR},
                )

    reputation = await fetch_reporter_reputation(session, row.client_id)
    return score, verified, reputation


async def fetch_detail_row(session: AsyncSession, report_id: uuid.UUID) -> Any | None:
    """Raw row, so the route can sign the photo URL before projecting."""
    return (await session.execute(_DETAIL_SQL, {"report_id": str(report_id)})).first()


# Row projectors


def row_to_list_item(row: Any) -> AdminReportListItem:
    location = location_or_none(row.report_lat, row.report_lng)
    building_centroid = location_or_none(row.building_lat, row.building_lng)
    geocode = location_or_none(getattr(row, "geocode_lat", None), getattr(row, "geocode_lon", None))
    source = getattr(row, "location_source", None)
    radius_m, area_only, confidence = geocode_meta(row, source)
    return AdminReportListItem(
        id=row.id,
        crisis_id=row.crisis_id,
        damage_class=row.damage_class,
        description=row.description,
        route_description=row.route_description,
        infra_type=list(row.infra_type) if row.infra_type is not None else None,
        infra_name=row.infra_name,
        crisis_type=row.crisis_type,
        crisis_type_detailed=row.crisis_type_detailed,
        debris=row.debris,
        building_id=row.building_id,
        client_id=row.client_id,
        client_submission_id=row.client_submission_id,
        location=location,
        map_point=location or building_centroid or geocode,
        location_source=source,
        location_radius_m=radius_m,
        location_area_only=area_only,
        location_confidence=confidence,
        photo_path=row.photo_path,
        created_at=row.created_at,
        confidence_score=float_or_none(getattr(row, "confidence_score", None)),
        verified=bool(getattr(row, "q_verified", False)),
    )


def _quality_from_row(row: Any) -> ReportQualityOut | None:
    """None when there is no quality row: confidence_score is NOT NULL on a present row."""
    if getattr(row, "confidence_score", None) is None:
        return None
    return ReportQualityOut(
        confidence_score=float_or_none(row.confidence_score),
        points=getattr(row, "quality_points", None),
        relevance_label=getattr(row, "quality_relevance_label", None),
        damage_agreement=getattr(row, "damage_agreement", None),
        photo_fresh=getattr(row, "photo_fresh", None),
        gps_pin_match=getattr(row, "gps_pin_match", None),
        corroborators_500m=getattr(row, "corroborators_500m", None),
        reporter_reputation=getattr(row, "reporter_reputation", None),
        verified=bool(getattr(row, "q_verified", False)),
        verified_at=getattr(row, "verified_at", None),
        is_duplicate_image=bool(getattr(row, "is_duplicate_image", False)),
        computed_at=getattr(row, "quality_computed_at", None),
    )


def row_to_detail(
    row: Any,
    *,
    photo_url: str | None,
    reporter_stats: ReporterReputationOut | None = None,
) -> AdminReportDetailResponse:
    return AdminReportDetailResponse(
        id=row.id,
        crisis_id=row.crisis_id,
        damage_class=as_damage_class(row.damage_class),
        description=row.description,
        route_description=row.route_description,
        infra_type=list(row.infra_type) if row.infra_type is not None else None,
        infra_name=row.infra_name,
        crisis_type=row.crisis_type,
        crisis_type_detailed=row.crisis_type_detailed,
        debris=as_debris(row.debris),
        building_id=row.building_id,
        building_name=getattr(row, "building_name", None),
        client_id=row.client_id,
        client_submission_id=row.client_submission_id,
        location=location_or_none(row.report_lat, row.report_lng),
        building_centroid=location_or_none(row.building_lat, row.building_lng),
        photo_path=row.photo_path,
        photo_url=photo_url,
        created_at=row.created_at,
        photo_metadata=photo_metadata_from_row(row),
        description_lang=getattr(row, "description_lang", None),
        description_en=getattr(row, "description_en", None),
        description_status=getattr(row, "description_status", None),
        route_description_lang=getattr(row, "route_description_lang", None),
        route_description_en=getattr(row, "route_description_en", None),
        route_description_status=getattr(row, "route_description_status", None),
        ai_caption=getattr(row, "ai_caption", None),
        ai_caption_status=getattr(row, "ai_caption_status", None),
        location_source=getattr(row, "location_source", None),
        quality=_quality_from_row(row),
        confidence_score=float_or_none(getattr(row, "confidence_score", None)),
        verified=bool(getattr(row, "q_verified", False)),
        reporter_stats=reporter_stats,
    )


def photo_metadata_from_row(row: Any) -> PhotoMetadata | None:
    """None when extracted_at is null: the PWA always stamps it when it sends EXIF."""
    extracted_at: datetime | None = getattr(row, "photo_exif_extracted_at", None)
    if extracted_at is None:
        return None
    raw_meta: object = row.photo_exif_meta
    meta: dict[str, Any] = cast(dict[str, Any], raw_meta) if isinstance(raw_meta, dict) else {}
    raw_camera: object = meta.get("camera")
    camera_dict: dict[str, Any] = (
        cast(dict[str, Any], raw_camera) if isinstance(raw_camera, dict) else {}
    )
    camera = (
        PhotoCamera(
            make=_str_or_none(camera_dict.get("make")),
            model=_str_or_none(camera_dict.get("model")),
            software=_str_or_none(camera_dict.get("software")),
        )
        if camera_dict
        else None
    )
    gps = (
        PhotoGps(
            latitude=row.photo_exif_lat,
            longitude=row.photo_exif_lng,
            accuracy=float_or_none(meta.get("gps_accuracy")),
        )
        if row.photo_exif_lat is not None and row.photo_exif_lng is not None
        else None
    )
    return PhotoMetadata(
        gps=gps,
        captured_at=row.photo_captured_at,
        orientation=_int_or_none(meta.get("orientation")),
        width=_int_or_none(meta.get("width")),
        height=_int_or_none(meta.get("height")),
        camera=camera,
        extracted_at=extracted_at,
    )


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _int_or_none(value: object) -> int | None:
    # JSON integers come back as `int`; reject bools (which subclass `int`).
    return value if isinstance(value, int) and not isinstance(value, bool) else None

    if isinstance(value, int | float):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
