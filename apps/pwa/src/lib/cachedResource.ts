// Synchronous SWR cache for non-critical GETs. Even a SW cache hit is async, so the last
// good value is mirrored to localStorage for flash-free first paint, then revalidated.

import { useCallback, useEffect, useState } from "react";
import { useLatest } from "../hooks/useLatest";
import { onIdle } from "./idle";
import { readJson, writeJson } from "./safeStorage";
import { STORAGE_EVENTS } from "./storageKeys";

const RESOURCE_UPDATED_EVENT = STORAGE_EVENTS.resourceUpdated;

interface Envelope<T> {
  data: T;
  cached_at: number;
}

export function readResource<T>(key: string): T | null {
  const parsed = readJson<Envelope<T>>(key);
  if (!parsed || typeof parsed !== "object" || !("data" in parsed)) return null;
  return parsed.data;
}

export function writeResource<T>(key: string, data: T): void {
  const envelope: Envelope<T> = { data, cached_at: Date.now() };
  // Quota or disabled storage (Safari private): best-effort, no event.
  if (!writeJson(key, envelope)) return;
  try {
    window.dispatchEvent(new CustomEvent(RESOURCE_UPDATED_EVENT, { detail: { key } }));
  } catch {
    // best-effort
  }
}

// Coalesced per key so the warming ladder and a screen mount don't double-fetch.
const inflight = new Map<string, Promise<void>>();
export function prefetchResource<T>(key: string, fetcher: () => Promise<T>): Promise<void> {
  const existing = inflight.get(key);
  if (existing) return existing;
  const p = (async () => {
    try {
      const data = await fetcher();
      writeResource(key, data);
    } catch {
      // best-effort — the SW cache / next mount will retry
    }
  })().finally(() => {
    inflight.delete(key);
  });
  inflight.set(key, p);
  return p;
}

interface UseCachedResourceResult<T> {
  data: T | null;
  /** True only while the *first* fetch (with no cached value) is in flight. */
  loading: boolean;
  refetch: () => void;
}

// Hydrates synchronously, then revalidates on idle, on same-key writes, on foreground and on reconnect.
export function useCachedResource<T>(
  key: string | null,
  fetcher: () => Promise<T>,
): UseCachedResourceResult<T> {
  const [data, setData] = useState<T | null>(() => (key ? readResource<T>(key) : null));
  const [loading, setLoading] = useState(false);

  // Callers often pass an inline closure; don't let it retrigger revalidation.
  const fetcherRef = useLatest(fetcher);

  const revalidate = useCallback(() => {
    if (!key) return;
    const had = readResource<T>(key);
    if (had === null) setLoading(true);
    void prefetchResource(key, fetcherRef.current).finally(() => {
      setData(readResource<T>(key));
      setLoading(false);
    });
  }, [key]);

  useEffect(() => {
    if (!key) {
      setData(null);
      return;
    }
    setData(readResource<T>(key));

    const idle = onIdle(() => revalidate());

    const onUpdated = (e: Event) => {
      const detail = (e as CustomEvent).detail as { key?: string } | undefined;
      if (detail?.key === key) setData(readResource<T>(key));
    };
    const onFocus = () => {
      if (document.visibilityState === "visible") revalidate();
    };
    const onOnline = () => revalidate();

    window.addEventListener(RESOURCE_UPDATED_EVENT, onUpdated);
    document.addEventListener("visibilitychange", onFocus);
    window.addEventListener("online", onOnline);

    return () => {
      idle.cancel();
      window.removeEventListener(RESOURCE_UPDATED_EVENT, onUpdated);
      document.removeEventListener("visibilitychange", onFocus);
      window.removeEventListener("online", onOnline);
    };
  }, [key, revalidate]);

  return { data, loading, refetch: revalidate };
}
