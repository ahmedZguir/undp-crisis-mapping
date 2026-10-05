-- Allow crises.status = 'inactive'. There is no DB default; the API sets it.

alter table public.crises
    drop constraint crises_status_check;

alter table public.crises
    add constraint crises_status_check
        check (status in ('inactive', 'active', 'archived'));
