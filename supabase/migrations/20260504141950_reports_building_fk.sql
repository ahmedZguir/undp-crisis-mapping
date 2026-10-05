-- reports.building_id FK. No on-delete clause: buildings are never hard-deleted.

alter table public.reports
    add column if not exists building_id uuid references public.buildings(id);

create index if not exists reports_building_id_idx
    on public.reports (building_id);
