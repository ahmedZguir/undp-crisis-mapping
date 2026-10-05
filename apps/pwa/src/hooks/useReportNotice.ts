import { useCallback, useEffect, useState } from "react";
import { type ReporterStats, reporterStatsKey } from "../api/reporter";
import { readResource } from "../lib/cachedResource";
import { getClientId } from "../lib/clientId";
import { loadDrafts, loadOutbox } from "../lib/db";
import { subscribeStoreChanges } from "../lib/db/notify";
import { onIdle } from "../lib/idle";
import {
  type NoticeInputs,
  REPORT_NOTICE_CHANGED_EVENT,
  type TabBadge,
  badgeFromUnseen,
  computeUnseen,
  markSeen,
  readSeen,
} from "../lib/reportNotice";
import { REPORTS_CACHE_UPDATED_EVENT, loadCachedReports } from "../lib/reportsCache";

// My Reports tab dot; recomputes on outbox/draft, reports-cache and seen-baseline changes.
export function useReportNotice(): { badge: TabBadge } {
  const [badge, setBadge] = useState<TabBadge>(null);

  const recompute = useCallback(async () => {
    const clientId = await getClientId().catch(() => null);
    if (!clientId) return;
    const [outboxRows, drafts] = await Promise.all([loadOutbox(), loadDrafts()]);
    const cached = loadCachedReports(clientId);
    // Cached stats, not /me/stats, to stay off the boot critical path.
    const stats = readResource<ReporterStats>(reporterStatsKey(clientId));
    const inputs: NoticeInputs = {
      submittedIds: (cached?.items ?? []).map((r) => r.id),
      badgeSlugs: (stats?.badges ?? []).map((b) => b.slug),
      outbox: outboxRows.map((r) => ({ id: r.id, status: r.status })),
      draftIds: drafts.map((d) => d.id),
    };
    const seen = readSeen();
    if (seen === null) {
      // First run: existing history counts as seen.
      markSeen(inputs);
      setBadge(null);
      return;
    }
    setBadge(badgeFromUnseen(computeUnseen(inputs, seen)));
  }, []);

  useEffect(() => {
    // First compute deferred so its IndexedDB reads don't contend with first paint.
    const idle = onIdle(() => void recompute());

    const onCacheUpdated = () => void recompute();
    const onNoticeChanged = () => void recompute();
    window.addEventListener(REPORTS_CACHE_UPDATED_EVENT, onCacheUpdated);
    window.addEventListener(REPORT_NOTICE_CHANGED_EVENT, onNoticeChanged);

    const unsubscribe = subscribeStoreChanges("all", () => void recompute());

    return () => {
      idle.cancel();
      window.removeEventListener(REPORTS_CACHE_UPDATED_EVENT, onCacheUpdated);
      window.removeEventListener(REPORT_NOTICE_CHANGED_EVENT, onNoticeChanged);
      unsubscribe();
    };
  }, [recompute]);

  return { badge };
}
