-- Web Mercator copy of map_point for server-side clustering.
--
-- Clustering snaps to a metre grid to match MapLibre's pixel-space radius; a
-- degree grid would distort N-S at mid latitudes. The grid is zoom-dependent,
-- so no index; bbox filters still use the GiST on map_point. The generated
-- column recomputes whenever the map_point triggers or a bulk UPDATE set it.

alter table public.reports
    add column map_point_3857 geometry(Point, 3857)
        generated always as (st_transform(map_point::geometry, 3857)) stored;
