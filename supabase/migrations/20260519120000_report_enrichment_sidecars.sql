-- Enrichment sidecars (translations, image captions) keep machine-derived data
-- off reports. Rows are created with the report, so each sub-job is a single
-- UPDATE. enrichment_finalized_at is set once every sub-job is terminal.

create table if not exists public.report_translations (
    report_id uuid primary key references public.reports(id) on delete cascade,
    description_lang text,
    description_en text,
    description_status text not null default 'pending'
        check (description_status in ('pending', 'ready', 'passthrough', 'skipped', 'failed')),
    description_error text,
    route_description_lang text,
    route_description_en text,
    route_description_status text not null default 'pending'
        check (route_description_status in ('pending', 'ready', 'passthrough', 'skipped', 'failed')),
    route_description_error text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.image_captions (
    report_id uuid primary key references public.reports(id) on delete cascade,
    caption text,
    relevance_label text check (relevance_label in ('relevant', 'irrelevant', 'unclear')),
    relevance_score numeric,
    status text not null default 'pending'
        check (status in ('pending', 'ready', 'failed')),
    error text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

alter table public.reports
    add column if not exists enrichment_finalized_at timestamptz;

-- Partial: finalized rows aren't part of the working set.
create index if not exists reports_enrichment_pending_idx
    on public.reports (crisis_id)
    where enrichment_finalized_at is null;

-- API-only data: RLS on with no policies.
alter table public.report_translations enable row level security;
alter table public.report_translations force row level security;
alter table public.image_captions enable row level security;
alter table public.image_captions force row level security;
