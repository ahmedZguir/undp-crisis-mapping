-- Reject reports whose GPS falls outside their crisis polygon. The API checks
-- this first; the trigger covers any writer that bypasses it. Raises 23514,
-- which the API maps to a 400.

create or replace function public.reports_gps_inside_crisis()
returns trigger
language plpgsql
as $$
declare
    poly geography;
begin
    if new.location is null then
        return new;
    end if;

    select c.geometry into poly
    from public.crises c
    where c.id = new.crisis_id;

    -- The reserved crisis has no polygon, so nothing to test against.
    if poly is null then
        return new;
    end if;

    if not st_contains(poly::geometry, new.location::geometry) then
        raise exception 'report location is outside the crisis area'
            using errcode = '23514',  -- check_violation
                  hint   = 'submit against a crisis whose area contains the GPS coordinate';
    end if;

    return new;
end;
$$;

drop trigger if exists reports_gps_inside_crisis_trg on public.reports;
create trigger reports_gps_inside_crisis_trg
    before insert or update of location, crisis_id
    on public.reports
    for each row
    execute function public.reports_gps_inside_crisis();
