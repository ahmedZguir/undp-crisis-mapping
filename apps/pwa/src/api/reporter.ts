import { API_BASE } from "../lib/apiBase";
import { readResource, writeResource } from "../lib/cachedResource";
import { STORAGE_KEYS, keyFor } from "../lib/storageKeys";
import { getCitizenReportHistory } from "./reports";

export interface Badge {
  slug: string;
  name: string;
  earned_at: string;
  report_id: string | null;
}

export interface ReporterStats {
  total_reports: number;
  points: number;
  badges: Badge[];
  newly_earned: Badge[];
}

// Display order. Mirrors backend `BADGE_CATALOG` (api/reports/badges.py) — keep in sync; legacy
// aliases (e.g. `quality_reporter`) are omitted on purpose.
export const BADGE_SLUGS = [
  "first_report",
  "ground_truth",
  "area_mapper",
  "active_responder",
  "community_anchor",
  "verified_by_coordinator",
] as const;

const MILESTONES = [5, 15, 30, 60, 100] as const;

function nextMilestone(points: number): number {
  return MILESTONES.find((m) => m > points) ?? MILESTONES[MILESTONES.length - 1] + 25;
}

function milestoneProgress(points: number): number {
  const prev = [...MILESTONES].reverse().find((m) => m <= points) ?? 0;
  const next = nextMilestone(points);
  if (next <= prev) return 1;
  return (points - prev) / (next - prev);
}

// 1-indexed count of milestones crossed (0–4 pts → Level 1, …, 100+ → Level 6).
function currentLevel(points: number): number {
  return MILESTONES.filter((m) => points >= m).length + 1;
}

export function levelProgress(points: number): {
  level: number;
  progress: number;
  remaining: number;
} {
  return {
    level: currentLevel(points),
    progress: milestoneProgress(points),
    remaining: nextMilestone(points) - points,
  };
}

// Count-based badges the stub can derive: [min reports, slug, name].
const STUB_BADGES = [
  [1, "first_report", "First Responder"],
  [5, "ground_truth", "Ground Truth"],
] as const;

// Fallback when /me/stats is unreachable; points stay 0 (server-scored only).
async function deriveStubStats(clientId: string): Promise<ReporterStats> {
  try {
    const history = await getCitizenReportHistory(clientId, { limit: 1 });
    const total = history.total;
    const earnedAt = new Date().toISOString();
    const badges: Badge[] = STUB_BADGES.filter(([min]) => total >= min).map(([, slug, name]) => ({
      slug,
      name,
      earned_at: earnedAt,
      report_id: null,
    }));
    return { total_reports: total, points: 0, badges, newly_earned: [] };
  } catch {
    return { total_reports: 0, points: 0, badges: [], newly_earned: [] };
  }
}

export function reporterStatsKey(clientId: string): string {
  return keyFor(STORAGE_KEYS.reporterStats, clientId);
}

export async function getReporterStats(clientId: string): Promise<ReporterStats> {
  try {
    const res = await fetch(`${API_BASE}/me/stats?client_id=${encodeURIComponent(clientId)}`);
    if (res.ok) {
      const stats = (await res.json()) as ReporterStats;
      // The server can't know what this device has seen, so diff against the cache here.
      // No cache (first load) means no "new badge" pop.
      const cached = readResource<ReporterStats>(reporterStatsKey(clientId));
      if (cached) {
        const seen = new Set(cached.badges.map((b) => b.slug));
        stats.newly_earned = stats.badges.filter((b) => !seen.has(b.slug));
      }
      // Cache only genuine server responses, never the stub.
      writeResource(reporterStatsKey(clientId), stats);
      return stats;
    }
  } catch {
    // fall through to stub
  }
  return deriveStubStats(clientId);
}
