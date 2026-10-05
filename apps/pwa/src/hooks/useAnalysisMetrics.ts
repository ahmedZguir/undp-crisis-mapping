import { useCallback, useEffect, useState } from "react";
import { getAnalysisMetrics } from "../api/admin";
import { errorMessage } from "../lib/errors";
import type { AnalysisMetrics } from "../types/admin";

// The endpoint runs a ~30s synchronous compute, too slow to repeat on every
// visit, so results are cached for the session. Recompute (`reload`) is the
// only way to force a fresh compute.
export interface UseAnalysisMetrics {
  data: AnalysisMetrics | null;
  loading: boolean;
  error: string | null;
  // Wall-clock ms when the held metrics were computed, or null if none yet.
  computedAt: number | null;
  reload: () => void;
}

interface CacheEntry {
  data: AnalysisMetrics;
  computedAt: number;
}

// In-memory cache keyed by crisis id. A full page reload starts fresh by design.
const cache = new Map<string, CacheEntry>();

export function useAnalysisMetrics(crisisId: string | null): UseAnalysisMetrics {
  const initial = crisisId ? cache.get(crisisId) : undefined;
  const [data, setData] = useState<AnalysisMetrics | null>(initial?.data ?? null);
  const [computedAt, setComputedAt] = useState<number | null>(initial?.computedAt ?? null);
  // Start loading when there is no cached snapshot, so the first paint shows
  // the skeleton instead of flashing an empty header-only frame.
  const [loading, setLoading] = useState(crisisId != null && !initial);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const reload = useCallback(() => {
    // Recompute drops the snapshot so the effect below misses the cache and
    // re-fetches; bumping reloadKey re-runs the effect even for the same crisis.
    if (crisisId) cache.delete(crisisId);
    setReloadKey((k) => k + 1);
  }, [crisisId]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: reloadKey is a manual refetch trigger, not read inside the effect
  useEffect(() => {
    if (!crisisId) {
      setData(null);
      setError(null);
      setLoading(false);
      setComputedAt(null);
      return;
    }
    const hit = cache.get(crisisId);
    if (hit) {
      setData(hit.data);
      setComputedAt(hit.computedAt);
      setError(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    setData(null);
    getAnalysisMetrics(crisisId, { signal: ctrl.signal })
      .then((m) => {
        if (cancelled) return;
        const now = Date.now();
        cache.set(crisisId, { data: m, computedAt: now });
        setData(m);
        setComputedAt(now);
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled || ctrl.signal.aborted) return;
        setError(errorMessage(err));
        setLoading(false);
      });
    return () => {
      cancelled = true;
      ctrl.abort();
    };
  }, [crisisId, reloadKey]);

  return { data, loading, error, computedAt, reload };
}
