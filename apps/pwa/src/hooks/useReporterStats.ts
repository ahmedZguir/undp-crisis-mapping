import { useCallback, useEffect, useState } from "react";
import { type ReporterStats, getReporterStats, reporterStatsKey } from "../api/reporter";
import { readResource } from "../lib/cachedResource";
import { getClientId, peekClientId } from "../lib/clientId";

function readCachedStats(): ReporterStats | null {
  const id = peekClientId();
  return id ? readResource<ReporterStats>(reporterStatsKey(id)) : null;
}

interface UseReporterStatsResult {
  stats: ReporterStats | null;
  loading: boolean;
  refetch: () => void;
}

export function useReporterStats(): UseReporterStatsResult {
  const [stats, setStats] = useState<ReporterStats | null>(readCachedStats);
  const [loading, setLoading] = useState(false);
  const [tick, setTick] = useState(0);

  // biome-ignore lint/correctness/useExhaustiveDependencies: tick is an intentional refetch counter
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void (async () => {
      const clientId = await getClientId();
      // Covers a first render before the client id resolved.
      const cached = readResource<ReporterStats>(reporterStatsKey(clientId));
      if (!cancelled && cached) setStats(cached);
      const result = await getReporterStats(clientId);
      if (cancelled) return;
      setStats(result);
      setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [tick]);

  const refetch = useCallback(() => setTick((n) => n + 1), []);

  return { stats, loading, refetch };
}
