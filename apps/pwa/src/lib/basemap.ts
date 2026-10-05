// OpenStreetMap raster basemap shared by citizen maps, admin maps and the offline tile warm.
// Both themes use OSM tiles (CARTO's anonymous dark basemap now demands an API key);
// dark mode is an `invert(1) hue-rotate(180deg)` canvas filter, which also flips data layers.

import type { Theme } from "./theme";

export const OSM_TILE_TEMPLATE = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";

const OSM_TILES = [OSM_TILE_TEMPLATE];

export function osmTileUrl(z: number, x: number, y: number): string {
  return OSM_TILE_TEMPLATE.replace("{z}", String(z))
    .replace("{x}", String(x))
    .replace("{y}", String(y));
}

export const BASEMAP_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

export function basemapTiles(_theme: Theme): string[] {
  return OSM_TILES;
}
