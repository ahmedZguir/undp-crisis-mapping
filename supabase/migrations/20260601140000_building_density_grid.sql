-- Precomputed building counts per lon/lat cell, so the ingest size estimate
-- sums local cells instead of scanning the Overture parquet on S3.
--
-- Built offline per Overture release as a superuser; the runtime only reads.
-- Cell size must match DENSITY_GRID_STEP_DEG in api/buildings/density_grid.py.
-- Each building counts in the cell of its bbox centre.

create table public.building_density_grid (
  release text not null,
  -- ix = floor(centre_lon / step), iy = floor(centre_lat / step)
  ix integer not null,
  iy integer not null,
  n bigint not null,
  primary key (release, ix, iy)
);

comment on table public.building_density_grid is
  'Precomputed Overture building counts per (release, lon/lat cell) for fast '
  'ingest-size estimates. Derived/offline-built; runtime reads only. See '
  'docs/decisions/01_06-building-density-grid.md.';

-- The PK serves the estimator's range scan.

grant select on public.building_density_grid to api_service;
