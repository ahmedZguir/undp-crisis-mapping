-- Read indexes for large crises.
--
-- Map reads filter on coalesce(location, building centroid, geocode point),
-- which spans three tables and can't be indexed, so it is materialised as
-- map_point with a GiST index. map_point_source lets public and export paths
-- exclude AI geocodes while still using the index.
--
-- The triggers do per-row lookups. For bulk loads, disable them and set
-- map_point with one set-based UPDATE afterwards.

alter table public.reports
    add column map_point geography(Point, 4326),
    add column map_point_source text
        check (map_point_source in ('submitted_pin', 'building_centroid', 'ai_geocode'));

-- Backfill. Correlated subqueries so a missing building or geocode never drops
-- a row.
update public.reports r
set map_point = coalesce(
        r.location,
        (select b.centroid from public.buildings b where b.id = r.building_id),
        (select st_setsrid(st_makepoint(g.lon, g.lat), 4326)::geography
           from public.report_geocodes g
          where g.report_id = r.id and g.lat is not null and g.lon is not null)
    ),
    map_point_source = case
        when r.location is not null then 'submitted_pin'
        when exists (
            select 1 from public.buildings b
            where b.id = r.building_id and b.centroid is not null
        ) then 'building_centroid'
        when exists (
            select 1 from public.report_geocodes g
            where g.report_id = r.id and g.lat is not null and g.lon is not null
        ) then 'ai_geocode'
        else null
    end;

create index reports_map_point_gix
    on public.reports using gist (map_point);

-- Dashboard aggregates filter crisis + damage_class and order by recency.
create index reports_crisis_damage_created_idx
    on public.reports (crisis_id, damage_class, created_at desc);

-- Array overlap/containment predicates on infra_type (public buildings, filters).
create index reports_infra_type_gin
    on public.reports using gin (infra_type);

-- The geocode arrives later, in another table, so triggers on both tables keep
-- map_point current.

-- Recompute from the report's own inputs plus any resolved geocode.
create or replace function public.reports_set_map_point()
returns trigger
language plpgsql
as $$
declare
    bld_centroid geography;
    geo_lat double precision;
    geo_lon double precision;
begin
    if new.building_id is not null then
        select b.centroid into bld_centroid
        from public.buildings b
        where b.id = new.building_id;
    end if;

    select g.lat, g.lon into geo_lat, geo_lon
    from public.report_geocodes g
    where g.report_id = new.id;

    if new.location is not null then
        new.map_point := new.location;
        new.map_point_source := 'submitted_pin';
    elsif bld_centroid is not null then
        new.map_point := bld_centroid;
        new.map_point_source := 'building_centroid';
    elsif geo_lat is not null and geo_lon is not null then
        new.map_point := st_setsrid(st_makepoint(geo_lon, geo_lat), 4326)::geography;
        new.map_point_source := 'ai_geocode';
    else
        new.map_point := null;
        new.map_point_source := null;
    end if;

    return new;
end;
$$;

drop trigger if exists reports_set_map_point_trg on public.reports;
create trigger reports_set_map_point_trg
    before insert or update of location, building_id
    on public.reports
    for each row
    execute function public.reports_set_map_point();

-- Recompute when the geocode resolves. It only updates map_point columns, so
-- the reports trigger (on location/building_id) doesn't re-fire.
create or replace function public.report_geocodes_sync_map_point()
returns trigger
language plpgsql
as $$
begin
    update public.reports r
    set map_point = coalesce(
            r.location,
            (select b.centroid from public.buildings b where b.id = r.building_id),
            case when new.lat is not null and new.lon is not null
                 then st_setsrid(st_makepoint(new.lon, new.lat), 4326)::geography
                 else null end
        ),
        map_point_source = case
            when r.location is not null then 'submitted_pin'
            when exists (
                select 1 from public.buildings b
                where b.id = r.building_id and b.centroid is not null
            ) then 'building_centroid'
            when new.lat is not null and new.lon is not null then 'ai_geocode'
            else null
        end
    where r.id = new.report_id;
    return new;
end;
$$;

drop trigger if exists report_geocodes_sync_map_point_trg on public.report_geocodes;
create trigger report_geocodes_sync_map_point_trg
    after insert or update of lat, lon
    on public.report_geocodes
    for each row
    execute function public.report_geocodes_sync_map_point();
