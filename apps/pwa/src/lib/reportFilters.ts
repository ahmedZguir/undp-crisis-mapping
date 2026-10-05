import type { LocationKindFilter } from "../api/search";
import type { AdminReportDebris, AdminReportListItem } from "../types/admin";

export type TimeWindow = "any" | "24h" | "7d" | "30d" | "custom";
export type DebrisFilter = "any" | AdminReportDebris;
// `exact` = GPS pin or matched building centroid; `ai_geocode` = placed only by
// the AI geocode of the route text. Mirrors `location_source` so the filter,
// the map and the Locations KPI cell agree.
export type LocationFilter = LocationKindFilter;

/** Client-side filters over the loaded sample, on top of the server-side
 *  `damage_class` filter (a separate prop). Filtering here never refetches. */
export interface ReportFilters {
  timeWindow: TimeWindow;
  // Bounds for `timeWindow === "custom"` as `YYYY-MM-DD` (the native date input
  // value), so the picker round-trips losslessly. Either end may be open.
  customFrom: string | null;
  customTo: string | null;
  debris: DebrisFilter;
  location: LocationFilter;
  infraTypes: string[];
}

export const DEFAULT_REPORT_FILTERS: ReportFilters = {
  timeWindow: "any",
  customFrom: null,
  customTo: null,
  debris: "any",
  location: "any",
  infraTypes: [],
};

const DAY_MS = 24 * 60 * 60 * 1000;

/** `YYYY-MM-DD` to UTC start-of-day epoch ms, or null. UTC matches the server,
 *  which casts the same string to `timestamptz` (see `admin_search.py`). */
function dayStartMs(date: string | null): number | null {
  if (!date) return null;
  const ms = new Date(`${date}T00:00:00Z`).getTime();
  return Number.isNaN(ms) ? null : ms;
}

/** Any time window as `[fromMs, toMs]` (null = open). Shared by the client
 *  filter and the server payload so the two always agree. */
export function resolveTimeRange(
  f: ReportFilters,
  nowMs: number,
): { fromMs: number | null; toMs: number | null } {
  if (f.timeWindow === "custom") {
    let fromMs = dayStartMs(f.customFrom);
    // The `to` bound is inclusive of the whole calendar day picked.
    const toStart = dayStartMs(f.customTo);
    let toMs = toStart === null ? null : toStart + DAY_MS - 1;
    // A reversed range is a misclick; swap rather than return nothing.
    if (fromMs !== null && toStart !== null && toMs !== null && fromMs > toMs) {
      const fromDayEnd = fromMs + DAY_MS - 1;
      fromMs = toStart;
      toMs = fromDayEnd;
    }
    return { fromMs, toMs };
  }
  const ms =
    f.timeWindow === "24h"
      ? DAY_MS
      : f.timeWindow === "7d"
        ? 7 * DAY_MS
        : f.timeWindow === "30d"
          ? 30 * DAY_MS
          : null;
  return { fromMs: ms === null ? null : nowMs - ms, toMs: null };
}

/** `custom` with both ends open narrows nothing, so it doesn't count. */
export function timeFilterActive(f: ReportFilters): boolean {
  if (f.timeWindow === "any") return false;
  if (f.timeWindow === "custom") return f.customFrom !== null || f.customTo !== null;
  return true;
}

export function applyClientFilters(
  items: AdminReportListItem[],
  f: ReportFilters,
): AdminReportListItem[] {
  const { fromMs, toMs } = resolveTimeRange(f, Date.now());
  return items.filter((r) => {
    if (fromMs !== null || toMs !== null) {
      const t = new Date(r.created_at).getTime();
      if (Number.isNaN(t)) return false;
      if (fromMs !== null && t < fromMs) return false;
      if (toMs !== null && t > toMs) return false;
    }
    if (f.debris !== "any" && r.debris !== f.debris) return false;
    if (
      f.location === "exact" &&
      r.location_source !== "submitted_pin" &&
      r.location_source !== "building_centroid"
    )
      return false;
    if (f.location === "ai_geocode" && r.location_source !== "ai_geocode") return false;
    if (f.infraTypes.length > 0) {
      if (!r.infra_type || r.infra_type.length === 0) return false;
      if (!r.infra_type.some((t) => f.infraTypes.includes(t))) return false;
    }
    return true;
  });
}

/** Distinct infra-type tokens in the loaded sample, sorted, for the chip group. */
export function collectInfraTypes(items: { infra_type: string[] | null }[]): string[] {
  const set = new Set<string>();
  for (const r of items) {
    if (!r.infra_type) continue;
    for (const t of r.infra_type) set.add(t);
  }
  return [...set].sort();
}
