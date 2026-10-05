-- Anon SELECT policies for crises in public full mode. They OR with the
-- buildings-mode policies, so anon sees visible rows from crises in either mode.

-- Visible reports on active, non-reserved crises in full mode.
drop policy if exists anon_select_reports_for_full_mode on public.reports;

create policy anon_select_reports_for_full_mode on public.reports
    for select
    to anon
    using (
        public_visible = true
        and exists (
            select 1 from public.crises c
            where c.id = reports.crisis_id
              and c.status = 'active'
              and c.name <> 'Other / Unspecified'
              and c.public_visibility = 'full'
        )
    );

-- Buildings tied to such a report. As with buildings mode, this only takes
-- effect if the open anon_select_buildings policy is tightened.
drop policy if exists anon_select_buildings_for_full_mode on public.buildings;

create policy anon_select_buildings_for_full_mode on public.buildings
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
               and c.public_visibility = 'full'
        )
    );
