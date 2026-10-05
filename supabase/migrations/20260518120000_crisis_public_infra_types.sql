-- crises.public_infra_types: optional whitelist of infra types shown publicly
-- (NULL means no filter). The anon report policies apply it too; the buildings
-- policies reach through reports, so they inherit it.

alter table public.crises
    add column if not exists public_infra_types text[];

-- A non-null whitelist excludes reports with NULL infra_type.
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
              and (
                  c.public_infra_types is null
                  or reports.infra_type && c.public_infra_types
              )
        )
    );

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
              and (
                  c.public_infra_types is null
                  or reports.infra_type && c.public_infra_types
              )
        )
    );
