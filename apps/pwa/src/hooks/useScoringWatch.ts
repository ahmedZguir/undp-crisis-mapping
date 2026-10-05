import { useEffect } from "react";
import { getReporterStats } from "../api/reporter";
import { getClientId } from "../lib/clientId";
import {
  REPORTS_CACHE_UPDATED_EVENT,
  loadCachedReports,
  refreshReportsCache,
} from "../lib/reportsCache";

const POLL_MS = 8000;
// ~2.5 min cap until the next trigger.
const MAX_ATTEMPTS = 20;

// While a submitted report is still being scored, refresh the stats and reports caches so points
// and badges surface app-wide. Polling is transient and capped (an exception to the no-poll rule).
export function useScoringWatch(): void {
  useEffect(() => {
    let cancelled = false;
    let running = false;
    let timer = 0;

    function pending(clientId: string): boolean {
      const cached = loadCachedReports(clientId);
      return (cached?.items ?? []).some((i) => i.quality != null && !i.quality.scored);
    }

    function wait(ms: number): Promise<void> {
      return new Promise((resolve) => {
        timer = window.setTimeout(resolve, ms);
      });
    }

    async function run(): Promise<void> {
      // Guards re-entry from our own cache-updated event.
      if (running || cancelled) return;
      const clientId = await getClientId().catch(() => null);
      if (!clientId || cancelled || !pending(clientId)) return;
      running = true;
      try {
        let attempts = 0;
        while (!cancelled && attempts < MAX_ATTEMPTS) {
          // Stats first, so the cache-updated event below sees fresh badges.
          await getReporterStats(clientId).catch(() => undefined);
          await refreshReportsCache();
          if (cancelled || !pending(clientId)) break;
          attempts += 1;
          await wait(POLL_MS);
        }
      } finally {
        running = false;
      }
    }

    void run();
    const onCacheUpdated = () => void run();
    window.addEventListener(REPORTS_CACHE_UPDATED_EVENT, onCacheUpdated);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      window.removeEventListener(REPORTS_CACHE_UPDATED_EVENT, onCacheUpdated);
    };
  }, []);
}
