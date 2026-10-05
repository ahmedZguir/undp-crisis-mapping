-- Reporter rewards and report confidence.
--
-- report_quality is a 1:1 sidecar created with the report, so the worker only
-- UPDATEs. client_id is copied from reports so /me/stats is one indexed scan
-- here instead of a join against reports, which has no client_id index.

create table if not exists public.report_quality (
    report_id           uuid primary key references public.reports(id) on delete cascade,
    client_id           uuid,         -- null if the report had none
    -- citizen-facing
    has_photo           boolean not null default false,
    has_description     boolean not null default false,
    relevance_label     text check (relevance_label in ('relevant', 'irrelevant', 'unclear')),
    damage_agreement    boolean,      -- null = N/A (no photo / low classifier confidence)
    points              smallint not null default 0,  -- 0..5, frozen at compute time
    -- confidence factors
    photo_fresh         boolean,      -- null = no capture timestamp
    gps_pin_match       boolean,      -- null = no EXIF GPS
    corroborators_500m  int not null default 0,
    reporter_reputation int not null default 0,        -- prior verified reports by client
    confidence_score    numeric(4,3) not null default 0,  -- computed in code
    -- coordinator verify. verified_by is the auth user id, no FK into auth.
    verified            boolean not null default false,
    verified_by         uuid,
    verified_at         timestamptz,
    -- anti-gaming
    is_duplicate_image  boolean not null default false,
    -- bookkeeping
    computed_at         timestamptz,  -- null until the worker fills the row
    created_at          timestamptz not null default now()
);

-- /me/stats aggregates by client_id; this is the supporting index.
create index if not exists report_quality_client_id_idx
    on public.report_quality (client_id);

-- Exact-image dedup looks up earlier reports by photo_path (same client earns
-- nothing, a different client gets a confidence penalty).
create index if not exists reports_photo_path_idx
    on public.reports (photo_path)
    where photo_path is not null;

create table if not exists public.citizen_badges (
    id          uuid primary key default gen_random_uuid(),
    client_id   uuid not null,
    badge_slug  text not null,
    report_id   uuid references public.reports(id) on delete set null,
    earned_at   timestamptz not null default now(),
    unique (client_id, badge_slug)
);

create index if not exists citizen_badges_client_id_idx
    on public.citizen_badges (client_id);

-- API-only data: RLS on with no policies.
alter table public.report_quality enable row level security;
alter table public.report_quality force row level security;
alter table public.citizen_badges enable row level security;
alter table public.citizen_badges force row level security;
