-- Recreate the anon crisis and heat-cell policies to exclude the reserved
-- crisis by name, since its id is random at seed time.

drop policy if exists anon_select_active_crises on public.crises;
drop policy if exists anon_select_active_heat_cells on public.heat_cells;

create policy anon_select_active_crises on public.crises
    for select
    to anon
    using (
        status = 'active'
        and name <> 'Other / Unspecified'
    );

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
