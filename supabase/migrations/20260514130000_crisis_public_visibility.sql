-- crises.public_visibility (none | aggregate_view | buildings | full), and the
-- anon heat_cells policy now also requires aggregate_view so a leaked anon key
-- can't bypass the API's check.
--
-- The CHECK is added inside a do-block because add constraint has no
-- if not exists.

alter table public.crises
    add column if not exists public_visibility text not null default 'aggregate_view';

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'crises_public_visibility_chk'
          and conrelid = 'public.crises'::regclass
    ) then
        alter table public.crises
            add constraint crises_public_visibility_chk
            check (public_visibility in ('none', 'aggregate_view', 'buildings', 'full'));
    end if;
end
$$;

drop policy if exists anon_select_active_heat_cells on public.heat_cells;

create policy anon_select_active_heat_cells on public.heat_cells
    for select
    to anon
    using (
        exists (
            select 1 from public.crises c
            where c.id = heat_cells.crisis_id
              and c.status = 'active'
              and c.name <> 'Other / Unspecified'
              and c.public_visibility = 'aggregate_view'
        )
    );
