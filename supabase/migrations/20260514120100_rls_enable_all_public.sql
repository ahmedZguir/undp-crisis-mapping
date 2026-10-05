-- Enable and force RLS on every public table, then add the anon SELECT
-- policies the public surface needs.
--
-- FORCE makes the table owner (postgres) subject to RLS too; only BYPASSRLS
-- roles (api_service, service_role) skip it. spatial_ref_sys is skipped
-- because supabase_admin owns it.

do $$
declare
    t regclass;
begin
    for t in
        select c.oid::regclass
        from pg_class c
        join pg_namespace n on n.oid = c.relnamespace
        where n.nspname = 'public'
          and c.relkind = 'r'
          and c.relname <> 'spatial_ref_sys'
    loop
        execute format('alter table %s enable row level security', t::text);
        execute format('alter table %s force row level security', t::text);
    end loop;
end
$$;

-- anon can list active, non-reserved crises. The reserved row is matched by
-- name because its id is random at seed time.
create policy anon_select_active_crises on public.crises
    for select
    to anon
    using (
        status = 'active'
        and name <> 'Other / Unspecified'
    );

-- anon can read heat cells for active, non-reserved crises. k-anonymity is
-- applied in the API, not here.
create policy anon_select_active_heat_cells on public.heat_cells
    for select
    to anon
    using (
        exists (
            select 1 from public.crises c
            where c.id = heat_cells.crisis_id
              and c.status = 'active'
              and c.name <> 'Other / Unspecified'
        )
    );

-- anon can read building footprints so citizens can pick a building. They are
-- public Overture data and have no crisis_id, so a per-row crisis filter would
-- need an ST_Intersects on every read for no security gain.
create policy anon_select_buildings on public.buildings
    for select
    to anon
    using (true);

-- Every other table has no anon policy, so RLS denies it. authenticated gets
-- no policies either: coordinators go through the API, not PostgREST.
