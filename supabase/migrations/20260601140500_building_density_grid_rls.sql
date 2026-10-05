-- RLS on building_density_grid, which was created after the RLS baseline.
-- No policies: only BYPASSRLS roles (api_service, postgres) read or build it.

alter table public.building_density_grid enable row level security;
alter table public.building_density_grid force row level security;
