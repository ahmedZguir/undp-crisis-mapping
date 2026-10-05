-- Data retention: archiving a crisis starts a clock, and report_retention_days
-- (default 90) later a daily cron hard-deletes its reports. Disposals are
-- logged without PII.

-- NULL unless archived; cleared on un-archive.
alter table public.crises
    add column archived_at timestamptz;

-- Already-archived crises start their clock now, since their real archival
-- time is unknown.
update public.crises
   set archived_at = now()
 where status = 'archived' and archived_at is null;

-- Only crises the purge could act on are indexed.
create index crises_archived_at_purge_idx
    on public.crises (archived_at)
    where status = 'archived' and archived_at is not null;

-- Disposal audit: counts only, no PII. crisis_id has no FK so the log
-- outlives a hard-deleted crisis.
create table if not exists public.data_disposal_log (
    id            uuid primary key default gen_random_uuid(),
    crisis_id     uuid not null,
    reports_count int  not null,
    photos_count  int  not null,
    reason        text not null check (reason in ('retention', 'manual')),
    occurred_at   timestamptz not null default now()
);

-- RLS on with no policies; only the API reads it.
alter table public.data_disposal_log enable row level security;
alter table public.data_disposal_log force row level security;
