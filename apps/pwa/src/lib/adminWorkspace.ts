// Per-tab persistence for the reports dashboard's working state (filters,
// search inputs, view mode, inspector tab, export form), so it survives a
// refresh or a hop to the Analysis tab (which unmounts ReportsPage).
//
// Only the crisis belongs in the URL; view mode and filters are kept out of it.
// `sessionStorage` is scoped to one browser tab, so two tabs on two crises
// don't stomp each other, and it clears when the tab closes.
//
// State is one "last-used" set across crises; crisis-bound bits (location
// chips, infra-type tokens) reset at the call site. Summaries are the
// exception: cached per crisis + filter so a restored summary can never show
// another crisis's data.
//
// Best-effort: any storage failure degrades silently to in-memory defaults.

import type { SearchRequest } from "../api/search";
import { STORAGE_KEYS } from "./storageKeys";

const PREFIX = STORAGE_KEYS.adminWorkspace.prefix;

/** Read a persisted workspace slice, or `fallback` if absent/unparseable. */
export function readWorkspace<T>(key: string, fallback: T): T {
  try {
    const raw = sessionStorage.getItem(PREFIX + key);
    if (raw === null) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

/** Persist a workspace slice. No-op on any storage error. */
export function writeWorkspace<T>(key: string, value: T): void {
  try {
    sessionStorage.setItem(PREFIX + key, JSON.stringify(value));
  } catch {
    // Private mode, quota or disabled: in-memory only.
  }
}

// --- Summary cache ---
// The AI summary is an on-demand LLM stream and is never auto-rerun. Settled
// frames are cached by crisis + logical filter signature and restored on
// remount, so returning shows the last summary instead of an empty panel.

/**
 * Stable signature for a summary's scope. Excludes the map `bbox` (a refresh
 * re-frames the map slightly, which must not invalidate the restore) and
 * `limit` (never changes the scope). Any real filter change alters it.
 */
export function summarySignature(filter: SearchRequest): string {
  const { limit: _limit, location, ...rest } = filter;
  let loc: { division_ids?: string[]; polygon?: object } | undefined;
  if (location) {
    const { bbox: _bbox, division_ids, polygon } = location;
    if ((division_ids && division_ids.length > 0) || polygon !== undefined) {
      loc = { division_ids, polygon };
    }
  }
  return JSON.stringify(loc ? { ...rest, location: loc } : rest);
}

interface SummaryCacheEntry<F> {
  sig: string;
  frames: F[];
  generatedAt: number;
}

const summaryKey = (crisisId: string): string => `summary:${crisisId}`;

/** The caller must check `sig` against the current filter before using it. */
export function readSummaryCache<F>(crisisId: string): SummaryCacheEntry<F> | null {
  return readWorkspace<SummaryCacheEntry<F> | null>(summaryKey(crisisId), null);
}

export function writeSummaryCache<F>(
  crisisId: string,
  sig: string,
  frames: F[],
  generatedAt: number,
): void {
  writeWorkspace<SummaryCacheEntry<F>>(summaryKey(crisisId), { sig, frames, generatedAt });
}
