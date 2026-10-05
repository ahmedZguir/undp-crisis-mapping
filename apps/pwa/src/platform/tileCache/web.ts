// Web: the SW (sw-pmtiles.js + Workbox) does the caching; this only registers the pmtiles protocol.

import { Protocol } from "pmtiles";
import type { MapLibreModule, TileCacheAdapter } from "./index";

let registered = false;

export const webTileCache: TileCacheAdapter = {
  registerMapProtocols(maplibregl: MapLibreModule) {
    if (registered) return;
    registered = true;
    const protocol = new Protocol();
    maplibregl.addProtocol("pmtiles", protocol.tile.bind(protocol));
  },
  registerPmtiles() {},
  rasterTileUrl: (template) => template,
  vectorTileUrl: (template) => template,
  async warmRasterTile(url: string): Promise<void> {
    await fetch(url).catch(() => null);
  },
};
