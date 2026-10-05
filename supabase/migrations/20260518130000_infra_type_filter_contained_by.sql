-- Infra-type whitelist uses contained-by instead of overlap, so a report
-- tagged [residential, government] is hidden by a [residential] whitelist.
--
-- '{}' <@ anything is true, so cardinality(...) > 0 keeps reports with no
-- infra types excluded (it is NULL for NULL and 0 for '{}').

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
                  or (
                      cardinality(reports.infra_type) > 0
                      and reports.infra_type <@ c.public_infra_types
                  )
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
                  or (
                      cardinality(reports.infra_type) > 0
                      and reports.infra_type <@ c.public_infra_types
                  )
              )
        )
    );
