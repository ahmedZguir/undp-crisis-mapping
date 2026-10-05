// Tile cache / map protocol adapter. Web: the SW caches tiles. Native: a Filesystem cache behind
// `nativetile://` and a caching pmtiles Source.

import { isNativePlatform } from "../platformInfo";
import { nativeTileCache } from "./native";
import { webTileCache } from "./web";

// Type-only so the ~276 KB maplibre chunk stays out of the entry; map surfaces pass it in.
export type MapLibreModule = typeof import("maplibre-gl");

export interface TileCacheAdapter {
  // Idempotent.
  registerMapProtocols(maplibregl: MapLibreModule): void;
  // Call before adding the crisis's buildings source. No-op on web.
  registerPmtiles(url: string): void;
  // Keeps {z}/{x}/{y} placeholders intact. Passthrough on web.
  rasterTileUrl(template: string): string;
  vectorTileUrl(template: string): string;
  // Concrete URL; populates the cache under the same key the map render will use.
  warmRasterTile(url: string): Promise<void>;
}

export const tileCache: TileCacheAdapter = isNativePlatform() ? nativeTileCache : webTileCache;
