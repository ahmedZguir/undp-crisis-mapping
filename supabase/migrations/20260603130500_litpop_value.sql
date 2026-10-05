-- LitPop gridded asset value (USD per lon/lat cell) for the economic-damage
-- estimate in analysis reports.
--
-- Built offline per LitPop product as a superuser; the runtime only reads. An
-- empty table makes the economic figure show as pending. Cell size must match
-- LITPOP_GRID_STEP_DEG in api/analysis/constants.py. dataset tags the product
-- and country/year, so several can coexist.

create table if not exists public.litpop_value (
  -- e.g. 'litpop_2018_QAT'
  dataset text not null,
  -- ix = floor(lon / step), iy = floor(lat / step)
  ix integer not null,
  iy integer not null,
  usd double precision not null,
  primary key (dataset, ix, iy)
);

comment on table public.litpop_value is
  'Precomputed LitPop gridded asset value (USD per lon/lat cell) for the §10 '
  'economic-damage estimate. Derived/offline-built; runtime reads only. Empty '
  'until a LitPop product is loaded. See docs/decisions/03_06-crisis-analysis-reports.md.';

-- The PK serves the builder's range lookup.

grant select on public.litpop_value to api_service;

-- Internal reference data: RLS on with no policies.
alter table public.litpop_value enable row level security;
alter table public.litpop_value force row level security;
