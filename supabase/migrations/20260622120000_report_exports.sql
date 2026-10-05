-- report_exports: job state and permanent audit for photo-bundle exports, one
-- row per run.
--
-- At expiry the retention cron deletes the zip parts, clears parts and sets
-- status 'expired', but keeps the row as a record of who exported what.
-- filters stores the view payload, not a frozen id list; the worker resolves
-- the match set when it runs.
--
-- A 'running' row older than the worker job timeout is treated as stale by the
-- single-flight check, so a crashed worker can't block a crisis.

create table if not exists public.report_exports (
    id          uuid primary key default gen_random_uuid(),
    crisis_id   uuid not null references public.crises(id) on delete cascade,
    -- Email is snapshotted for the same reasons as
    -- crisis_reports.created_by_email.
    created_by        uuid not null,
    created_by_email  text,
    created_at        timestamptz not null default now(),

    -- 'expired' is the terminal state after the parts are purged.
    status          text not null default 'running'
        check (status in ('running', 'succeeded', 'failed', 'expired')),
    phase           text,
    progress_count  bigint,
    progress_total  bigint,
    error           text,
    ended_at        timestamptz,

    -- SearchRequest-shaped filter payload. '{}' for whole-crisis scope, which
    -- also includes reports without a location.
    filters         jsonb not null,
    scope           text check (scope in ('view', 'crisis')),
    -- Format of the manifest bundled with the photos.
    format          text check (format in ('csv', 'geojson')),

    -- [{key, bytes, photo_count}, ...] in part order. NULL until success and
    -- again after expiry.
    parts           jsonb,
    photo_count     bigint,
    total_bytes     bigint,
    -- NULL unless the run succeeded.
    expires_at      timestamptz
);

comment on table public.report_exports is
  'Job state + permanent audit for photo-bundle exports (one row per run). '
  'Heavy zip parts live in the private report-exports bucket (parts = pointers); '
  'purged at expires_at, row kept as audit (status=expired). filters is the '
  'captured view payload, re-resolved at run time. See '
  'docs/briefs/22_06-photo-export-image-bundles.md.';

-- Serves the history list and the single-flight running-row check.
create index if not exists report_exports_crisis_created_idx
    on public.report_exports (crisis_id, created_at desc);

-- API-only data: RLS on with no policies. Rows are never deleted, so no
-- DELETE grant.
alter table public.report_exports enable row level security;
alter table public.report_exports force row level security;

grant select, insert, update on public.report_exports to api_service;
