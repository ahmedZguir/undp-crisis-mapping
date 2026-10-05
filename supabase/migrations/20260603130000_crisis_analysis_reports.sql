-- crisis_reports: one row per generated crisis analysis report.
--
-- Unlike crisis_jobs, every generation is kept: the PDF is a point-in-time
-- claim that can't be reproduced later (data moves, the LLM narrative is
-- non-deterministic). The PDF lives in a private bucket; this row holds the
-- pointer and headline numbers. Progress columns match crisis_jobs.

create table if not exists public.crisis_reports (
    id          uuid primary key default gen_random_uuid(),
    crisis_id   uuid not null references public.crises(id) on delete cascade,
    -- Admin who triggered it. No FK into the auth schema.
    created_by  uuid not null,
    created_at  timestamptz not null default now(),

    -- 'running' until the worker sets a terminal status. phase is free text.
    status          text not null default 'running'
        check (status in ('running', 'succeeded', 'failed')),
    phase           text,
    progress_count  bigint,
    progress_total  bigint,
    error           text,
    ended_at        timestamptz,

    -- NULL until the run succeeds.
    storage_key text,

    -- Headline numbers for the cover and history list, set on success:
    --   device_count    distinct client_id values, a rough reach proxy
    --   building_count  buildings in the crisis AOI (coverage denominator)
    --   coverage_pct    % of those buildings with at least one report (0..100)
    report_count    bigint,
    device_count    bigint,
    building_count  bigint,
    coverage_pct    numeric
);

comment on table public.crisis_reports is
  'Immutable history of generated crisis analysis-report PDFs (one row per '
  'generation). Blob in private storage; this row holds the pointer + headline '
  'metadata. See docs/decisions/03_06-crisis-analysis-reports.md.';

create index if not exists crisis_reports_crisis_created_idx
    on public.crisis_reports (crisis_id, created_at desc);

-- API-only data: RLS on with no policies.
alter table public.crisis_reports enable row level security;
alter table public.crisis_reports force row level security;
