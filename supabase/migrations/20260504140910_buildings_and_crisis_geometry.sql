-- Buildings table and crisis geometry columns.
--
-- buildings has no crisis_id: crisis membership is a spatial intersection at
-- query time. centroid is a generated column so snapping can use a plain GiST
-- index. The reserved 'Other / Unspecified' crisis never gets a polygon.

create table if not exists public.buildings (
    id uuid primary key default gen_random_uuid(),
    source text not null,
    source_id text not null,
    footprint geography(MultiPolygon, 4326) not null,
    centroid geography(Point, 4326)
        generated always as (st_centroid(footprint::geometry)::geography) stored,
    name text,
    building_class text,
    height_m double precision,
    num_floors int,
    properties jsonb not null default '{}'::jsonb,
    ingested_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (source, source_id)
);

create index if not exists buildings_footprint_gix
    on public.buildings using gist (footprint);

create index if not exists buildings_centroid_gix
    on public.buildings using gist (centroid);

alter table public.crises
    add column if not exists geometry geography(MultiPolygon, 4326),
    add column if not exists overture_release_pinned text,
    add column if not exists pmtiles_url text,
    add column if not exists buildings_ingested_at timestamptz;
