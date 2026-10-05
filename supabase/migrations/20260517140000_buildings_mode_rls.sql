-- Anon SELECT policies for crises in public buildings mode, so a leaked anon
-- key can't read reports or buildings PostgREST-side beyond what the API shows.

-- Visible reports on active, non-reserved crises in buildings mode.
drop policy if exists anon_select_reports_for_buildings_mode on public.reports;

create policy anon_select_reports_for_buildings_mode on public.reports
    for select
    to anon
    using (
        public_visible = true
        and exists (
            select 1 from public.crises c
            where c.id = reports.crisis_id
              and c.status = 'active'
              and c.name <> 'Other / Unspecified'
              and c.public_visibility = 'buildings'
        )
    );

-- Buildings tied to such a report. anon_select_buildings still admits all
-- footprints and policies are OR'd, so this only takes effect if that policy
-- is ever tightened.
drop policy if exists anon_select_buildings_for_buildings_mode on public.buildings;

create policy anon_select_buildings_for_buildings_mode on public.buildings
    for select
    to anon
    using (
        exists (
            select 1
              from public.reports r
              join public.crises c on c.id = r.crisis_id
             where r.building_id = buildings.id
               and r.public_visible = true
               and c.status = 'active'
               and c.name <> 'Other / Unspecified'
               and c.public_visibility = 'buildings'
        )
    );
