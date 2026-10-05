// Offline cache of the citizen My Reports history. Signed photo URLs (~15 min TTL) are stored as-is;
// the screen treats their 4xx as a stale-URL signal and re-fetches.

import { type CitizenReportHistoryItem, getCitizenReportHistory } from "../api/reports";
import { getClientId } from "./clientId";
import { readJson, writeJson } from "./safeStorage";
import { STORAGE_EVENTS, STORAGE_KEYS, keyFor } from "./storageKeys";

export const REPORTS_CACHE_UPDATED_EVENT = STORAGE_EVENTS.reportsCacheUpdated;

interface CachedReports {
  items: CitizenReportHistoryItem[];
  cached_at: number;
}

function storageKey(clientId: string): string {
  return keyFor(STORAGE_KEYS.reportsCache, clientId);
}

export function loadCachedReports(clientId: string): CachedReports | null {
  const parsed = readJson<CachedReports>(storageKey(clientId));
  if (!parsed || !Array.isArray(parsed.items)) return null;
  return parsed;
}

export function saveCachedReports(clientId: string, items: CitizenReportHistoryItem[]): void {
  const payload: CachedReports = { items, cached_at: Date.now() };
  // Quota or disabled storage: best-effort, no event.
  if (!writeJson(storageKey(clientId), payload)) return;
  try {
    window.dispatchEvent(new CustomEvent(REPORTS_CACHE_UPDATED_EVENT));
  } catch {
    // best-effort
  }
}

// Best-effort; concurrent calls share one in-flight request.
let inflight: Promise<void> | null = null;
export function refreshReportsCache(): Promise<void> {
  if (inflight) return inflight;
  inflight = (async () => {
    try {
      const clientId = await getClientId();
      const { items } = await getCitizenReportHistory(clientId);
      saveCachedReports(clientId, items);
    } catch {
      // best-effort
    }
  })().finally(() => {
    inflight = null;
  });
  return inflight;
}
