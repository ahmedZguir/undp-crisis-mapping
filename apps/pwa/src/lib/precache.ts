// Best-effort offline-readiness pre-cache run after the language pick: crises list in localStorage,
// plus warm SW tile caches. Failures are swallowed so a fresh user is never blocked.

import { PMTiles } from "pmtiles";
import { getCrises } from "../api/reports";
import { tileCache } from "../platform/tileCache";
import type { Crisis } from "../types";
import { osmTileUrl } from "./basemap";
import { readJson, writeJson } from "./safeStorage";
import { STORAGE_KEYS } from "./storageKeys";

const CRISES_CACHE_KEY = STORAGE_KEYS.crisesCache.key;
const PRECACHED_KEY = STORAGE_KEYS.precachedFor.key; // value: `${crisisId}|${version}`

const OSM_WARM_ZOOMS = [13, 14];
const PMTILES_WARM_ZOOMS = [14, 15, 16];

interface CrisesCache {
  at: number;
  list: Crisis[];
}

export function readCrisesCache(): Crisis[] | null {
  const parsed = readJson<CrisesCache>(CRISES_CACHE_KEY);
  if (!parsed || !Array.isArray(parsed.list)) return null;
  return parsed.list;
}

function writeCrisesCache(list: Crisis[]) {
  const value: CrisesCache = { at: Date.now(), list };
  writeJson(CRISES_CACHE_KEY, value);
}

// The in-bounds tiles of the 3×3 grid around (lon, lat) at zoom `z`.
function neighbourTiles(lon: number, lat: number, z: number): Array<[number, number]> {
  const scale = 1 << z;
  const latRad = (lat * Math.PI) / 180;
  const cx = Math.floor(((lon + 180) / 360) * scale);
  const cy = Math.floor(
    ((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * scale,
  );
  const tiles: Array<[number, number]> = [];
  for (let dx = -1; dx <= 1; dx++) {
    for (let dy = -1; dy <= 1; dy++) {
      const tx = cx + dx;
      const ty = cy + dy;
      if (tx >= 0 && ty >= 0 && tx < scale && ty < scale) tiles.push([tx, ty]);
    }
  }
  return tiles;
}

// The SW's CacheFirst rule lands these in `osm-tiles-cache`.
export async function warmOsmTiles(centerLon: number, centerLat: number): Promise<void> {
  const fetches: Promise<unknown>[] = [];
  for (const z of OSM_WARM_ZOOMS) {
    for (const [tx, ty] of neighbourTiles(centerLon, centerLat, z)) {
      fetches.push(tileCache.warmRasterTile(osmTileUrl(z, tx, ty)));
    }
  }
  await Promise.all(fetches);
}

// Avoids cold Range-request waterfalls on first map open. Must run AFTER sendTileVersion so the SW
// has switched to the right cache bucket.
export async function warmPmtiles(pmtilesUrl: string | null | undefined): Promise<void> {
  if (!pmtilesUrl) return;
  const httpUrl = pmtilesUrl.startsWith("pmtiles://")
    ? pmtilesUrl.slice("pmtiles://".length)
    : pmtilesUrl;
  try {
    const p = new PMTiles(httpUrl);
    const header = await p.getHeader();

    const { centerLon, centerLat, minZoom, maxZoom } = header;
    // (0, 0) centre means the file has no bbox metadata.
    if (Math.abs(centerLon) < 0.001 && Math.abs(centerLat) < 0.001) return;

    void warmOsmTiles(centerLon, centerLat);

    // getZxy() drives Range requests the SW intercepts and caches.
    const zooms = PMTILES_WARM_ZOOMS.filter((z) => z >= minZoom && z <= maxZoom);
    const fetches: Promise<unknown>[] = [];
    for (const z of zooms) {
      for (const [tx, ty] of neighbourTiles(centerLon, centerLat, z)) {
        fetches.push(p.getZxy(z, tx, ty).catch(() => null));
      }
    }
    await Promise.all(fetches);
  } catch {
    // best-effort
  }
}

function precachedKey(crisisId: string, version: string | null | undefined): string {
  return `${crisisId}|${version ?? ""}`;
}

// Write-only: nothing reads PRECACHED_KEY; kept so stored state doesn't change.
function markPrecached(crisis: Crisis): void {
  try {
    localStorage.setItem(PRECACHED_KEY, precachedKey(crisis.id, crisis.overture_release_pinned));
  } catch {
    // ignore
  }
}

interface PrecacheResult {
  /** Set only when exactly one crisis is active. */
  autoSelectedCrisis: Crisis | null;
  crises: Crisis[];
  fresh: boolean;
}

// Idempotent. The caller persists any auto-selected crisis and calls sendTileVersion().
export async function runPrecache(): Promise<PrecacheResult> {
  let crises: Crisis[] = [];
  let fresh = false;
  try {
    crises = await getCrises();
    fresh = true;
    writeCrisesCache(crises);
  } catch {
    crises = readCrisesCache() ?? [];
  }

  const autoSelected = crises.length === 1 ? crises[0] : null;

  if (autoSelected) {
    markPrecached(autoSelected);
  }

  return { autoSelectedCrisis: autoSelected, crises, fresh };
}
