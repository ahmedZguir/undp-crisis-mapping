// Tile get-or-fetch with the SW CacheFirst eviction shape (max age + max entries, oldest out).

export interface StoredTile {
  bytes: Uint8Array;
  storedAt: number;
}

export interface TileStore {
  get(key: string): Promise<StoredTile | null>;
  put(key: string, bytes: Uint8Array, storedAt: number): Promise<void>;
  delete(key: string): Promise<void>;
  entries(): Promise<{ key: string; storedAt: number }[]>;
}

interface TileCachePolicy {
  maxEntries: number;
  maxAgeMs: number;
}

export interface TileCache {
  fetchThrough(key: string, fetcher: () => Promise<Uint8Array>, now: number): Promise<Uint8Array>;
  evict(now: number): Promise<void>;
}

export function createTileCache(store: TileStore, policy: TileCachePolicy): TileCache {
  async function evict(now: number): Promise<void> {
    const all = await store.entries();
    const live: { key: string; storedAt: number }[] = [];
    for (const e of all) {
      if (now - e.storedAt > policy.maxAgeMs) {
        await store.delete(e.key);
      } else {
        live.push(e);
      }
    }
    if (live.length > policy.maxEntries) {
      live.sort((a, b) => a.storedAt - b.storedAt);
      const excess = live.length - policy.maxEntries;
      for (let i = 0; i < excess; i++) await store.delete(live[i].key);
    }
  }

  return {
    evict,
    async fetchThrough(key, fetcher, now) {
      // Best-effort: a Filesystem hiccup must never break tile loading.
      try {
        const hit = await store.get(key);
        if (hit && now - hit.storedAt <= policy.maxAgeMs) {
          return hit.bytes;
        }
      } catch {
        // fetch fresh
      }
      const bytes = await fetcher();
      await store.put(key, bytes, now).catch(() => {});
      void evict(now);
      return bytes;
    },
  };
}
