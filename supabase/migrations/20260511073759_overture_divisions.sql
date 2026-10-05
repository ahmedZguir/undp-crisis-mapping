-- Overture divisions (one row per division) and division areas (polygons).
--
-- names_search_text is names_primary plus all names_common values, filled by
-- the ingest script because a generated column can't flatten the JSONB. Its
-- trigram index serves both primary-name and cross-language matches.

create extension if not exists pg_trgm;

create table if not exists public.overture_divisions (
    id text primary key,
    subtype text not null,
    country text,
    names_primary text not null,
    names_common jsonb not null default '{}'::jsonb,
    names_search_text text not null,
    population bigint,
    bbox_xmin double precision not null,
    bbox_ymin double precision not null,
    bbox_xmax double precision not null,
    bbox_ymax double precision not null,
    hierarchy_parents text[] not null default '{}'::text[],
    point_geometry geography(Point, 4326),
    release text not null,
    ingested_at timestamptz not null default now()
);

create index if not exists overture_divisions_names_search_trgm
    on public.overture_divisions using gin (names_search_text gin_trgm_ops);

create index if not exists overture_divisions_country
    on public.overture_divisions (country);

create index if not exists overture_divisions_country_subtype_country
    on public.overture_divisions (country)
    where subtype = 'country';

create index if not exists overture_divisions_population
    on public.overture_divisions (population desc nulls last);

create table if not exists public.overture_division_areas (
    id text primary key,
    division_id text not null
        references public.overture_divisions(id) on delete cascade,
    subtype text not null,
    country text,
    geometry geography(MultiPolygon, 4326) not null,
    release text not null,
    ingested_at timestamptz not null default now()
);

create index if not exists overture_division_areas_division_id
    on public.overture_division_areas (division_id);

create index if not exists overture_division_areas_country_subtype_country
    on public.overture_division_areas (country)
    where subtype = 'country';

create index if not exists overture_division_areas_geometry_gix
    on public.overture_division_areas using gist (geometry);
