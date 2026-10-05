import { beforeEach, describe, expect, it, vi } from "vitest";
import { type StoredTile, type TileStore, createTileCache } from "./cacheCore";

function fakeStore(): TileStore & { size: () => number } {
  const map = new Map<string, StoredTile>();
  return {
    async get(key) {
      return map.get(key) ?? null;
    },
    async put(key, bytes, storedAt) {
      map.set(key, { bytes, storedAt });
    },
    async delete(key) {
      map.delete(key);
    },
    async entries() {
      return [...map.entries()].map(([key, v]) => ({ key, storedAt: v.storedAt }));
    },
    size: () => map.size,
  };
}

const bytes = (s: string) => new TextEncoder().encode(s);

describe("createTileCache.fetchThrough", () => {
  let store: ReturnType<typeof fakeStore>;
  beforeEach(() => {
    store = fakeStore();
  });

  it("fetches and stores on a miss, then serves from cache", async () => {
    const cache = createTileCache(store, { maxEntries: 100, maxAgeMs: 1000 });
    const fetcher = vi.fn(async () => bytes("tile"));

    const a = await cache.fetchThrough("k", fetcher, 0);
    const b = await cache.fetchThrough("k", fetcher, 500);

    expect(new TextDecoder().decode(a)).toBe("tile");
    expect(new TextDecoder().decode(b)).toBe("tile");
    expect(fetcher).toHaveBeenCalledTimes(1); // second was a cache hit
  });

  it("re-fetches once the entry is older than maxAgeMs", async () => {
    const cache = createTileCache(store, { maxEntries: 100, maxAgeMs: 1000 });
    const fetcher = vi.fn(async () => bytes("tile"));

    await cache.fetchThrough("k", fetcher, 0);
    await cache.fetchThrough("k", fetcher, 1001); // stale → refetch

    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});

describe("createTileCache.evict", () => {
  it("drops aged-out entries", async () => {
    const store = fakeStore();
    await store.put("old", bytes("x"), 0);
    await store.put("fresh", bytes("y"), 900);
    const cache = createTileCache(store, { maxEntries: 100, maxAgeMs: 1000 });

    await cache.evict(1500); // old: 1500-0 > 1000 → drop; fresh: 1500-900 < 1000 → keep

    expect(await store.get("old")).toBeNull();
    expect(await store.get("fresh")).not.toBeNull();
  });

  it("trims to maxEntries, oldest first", async () => {
    const store = fakeStore();
    await store.put("a", bytes("a"), 1);
    await store.put("b", bytes("b"), 2);
    await store.put("c", bytes("c"), 3);
    const cache = createTileCache(store, { maxEntries: 2, maxAgeMs: 1_000_000 });

    await cache.evict(10);

    expect(store.size()).toBe(2);
    expect(await store.get("a")).toBeNull(); // oldest evicted
    expect(await store.get("c")).not.toBeNull();
  });
});
