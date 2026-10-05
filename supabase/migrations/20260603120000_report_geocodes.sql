-- report_geocodes: machine-derived location for a report's free-text
-- route_description, kept off reports because it is a guess, not citizen data.
-- The row is created with the report, so the worker only ever UPDATEs.
--
-- status: pending, ready (lat/lon NULL when nothing could be located),
-- skipped (has GPS/building or no text), failed.

create table if not exists public.report_geocodes (
    report_id        uuid primary key references public.reports(id) on delete cascade,
    lat              double precision,
    lon              double precision,
    polygon          jsonb,                 -- GeoJSON when available (areas); NULL for point-only POIs
    granularity_tier text,                  -- poi | building | street | neighborhood | city | county | state | country
    radius_m         double precision,      -- uncertainty radius driving the pin-vs-area decision
    area_only        boolean,               -- render as a shaded area, not a pin
    confidence       numeric,               -- 0..1: name match, tier granularity and AOI fit
    source           text not null default 'osm',
    toponyms         jsonb,                 -- per-mention resolutions, for audit
    status           text not null default 'pending'
        check (status in ('pending', 'ready', 'skipped', 'failed')),
    error            text,
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now()
);

-- Partial: only pending rows are in the working set.
create index if not exists report_geocodes_pending_idx
    on public.report_geocodes (report_id)
    where status = 'pending';

-- API-only data: RLS on with no policies.
alter table public.report_geocodes enable row level security;
alter table public.report_geocodes force row level security;
