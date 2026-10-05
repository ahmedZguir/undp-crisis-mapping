-- Spine migration: PostGIS, crises, reports, reserved row, private storage bucket.

create extension if not exists postgis;

create table if not exists public.crises (
    id uuid primary key default gen_random_uuid(),
    name text not null,
    status text not null check (status in ('active', 'archived')),
    created_at timestamptz not null default now()
);

create unique index if not exists crises_name_uniq on public.crises (name);

insert into public.crises (name, status)
values ('Other / Unspecified', 'active')
on conflict (name) do nothing;

create table if not exists public.reports (
    id uuid primary key default gen_random_uuid(),
    crisis_id uuid not null references public.crises(id),
    damage_class text not null check (damage_class in ('minimal', 'partial', 'complete')),
    description text,
    photo_path text not null,
    location geography(Point, 4326),
    device_id text,
    created_at timestamptz not null default now()
);

create index if not exists reports_crisis_id_idx on public.reports (crisis_id);

insert into storage.buckets (id, name, public)
values ('report-photos', 'report-photos', false)
on conflict (id) do nothing;
