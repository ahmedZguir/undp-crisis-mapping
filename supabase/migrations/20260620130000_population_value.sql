-- Gridded residential population (WorldPop constrained 100m, summed per
-- lon/lat cell) for the displaced-people estimate. Without coverage the
-- estimate falls back to 3-6 occupants per building.
--
-- Built offline per country as a superuser; the runtime only reads. Cell size
-- (POPULATION_GRID_STEP_DEG) must equal the density grid's, because the
-- builder divides population by the building count of the same cell. dataset
-- tags the product and country/year, so several can coexist.

create table if not exists public.population_value (
  -- e.g. 'worldpop_2025_LBY'
  dataset text not null,
  -- ix = floor(lon / step), iy = floor(lat / step)
  ix integer not null,
  iy integer not null,
  people double precision not null,
  primary key (dataset, ix, iy)
);

comment on table public.population_value is
  'Precomputed gridded residential population (people per lon/lat cell) for the '
  '§10 displaced-people estimate. Derived/offline-built from WorldPop constrained '
  '100m; runtime reads only. Empty until a country is loaded; absence falls back '
  'to the 3-6 occupancy band. See docs/decisions/population-affected-estimate.md.';

-- The PK serves the builder's range lookup.

grant select on public.population_value to api_service;

-- Internal reference data: RLS on with no policies.
alter table public.population_value enable row level security;
alter table public.population_value force row level security;
