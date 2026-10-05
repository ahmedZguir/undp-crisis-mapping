// Warming ladder: warm heavy code chunks and non-critical data on idle, one step ahead of need.

import { getReporterStats, reporterStatsKey } from "../api/reporter";
import { crisisStatsKey, getCrisisStats } from "../api/reports";
import { prefetchResource } from "./cachedResource";
import { onIdle } from "./idle";
import { refreshReportsCache } from "./reportsCache";

const warmedChunks = new Set<string>();
function warmChunkOnce(name: string, load: () => Promise<unknown>): void {
  if (warmedChunks.has(name)) return;
  warmedChunks.add(name);
  onIdle(() => {
    void load().catch(() => {
      // Let a later trigger retry (e.g. offline first load).
      warmedChunks.delete(name);
    });
  });
}

export function warmExifr(): void {
  warmChunkOnce("exifr", () => import("exifr"));
}

// ~276 KB gz maplibre + pmtiles, shared by the location step and browse map.
export function warmMapChunk(): void {
  warmChunkOnce("map", () => import("../components/BrowseMap"));
}

// Not memoized: re-entering home revalidates (prefetches are coalesced).
export function warmHomeData(crisisId: string | null, clientId: string | null): void {
  onIdle(() => {
    if (crisisId) void prefetchResource(crisisStatsKey(crisisId), () => getCrisisStats(crisisId));
    if (clientId) {
      void prefetchResource(reporterStatsKey(clientId), () => getReporterStats(clientId));
    }
    void refreshReportsCache();
  });
}
