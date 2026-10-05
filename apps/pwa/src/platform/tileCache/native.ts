// Native Filesystem tile cache: raster/vector tiles via `nativetile://`, PMTiles ranges via a
// per-crisis CachingSource. DEVICE-VERIFY.

import { CapacitorHttp } from "@capacitor/core";
import { FetchSource, PMTiles, Protocol, type RangeResponse, type Source } from "pmtiles";
import { NATIVE_API_BASE, NATIVE_NGROK_BASIC_AUTH } from "../../config";
import { base64ToBytes } from "../../lib/base64";
import { platformName } from "../platformInfo";
import { type TileCache, type TileStore, createTileCache } from "./cacheCore";
import { createFilesystemTileStore } from "./filesystemStore";
import type { MapLibreModule, TileCacheAdapter } from "./index";

// Approximates the SW's CacheFirst window.
const POLICY = { maxEntries: 3000, maxAgeMs: 30 * 24 * 60 * 60 * 1000 };

let storePromise: Promise<TileStore> | null = null;
function getStore(): Promise<TileStore> {
  if (!storePromise) storePromise = createFilesystemTileStore();
  return storePromise;
}

let cachePromise: Promise<TileCache> | null = null;
function getCache(): Promise<TileCache> {
  if (!cachePromise) cachePromise = getStore().then((store) => createTileCache(store, POLICY));
  return cachePromise;
}

// Network-first for raster/vector: cache-first would do a bridge readFile+stat per tile on every pan.
// PMTiles ranges stay cache-first (immutable, re-read often).
const writtenTiles = new Set<string>();
let writeCount = 0;
async function networkFirstTile(
  key: string,
  fetcher: () => Promise<Uint8Array>,
): Promise<Uint8Array> {
  try {
    const bytes = await fetcher();
    if (!writtenTiles.has(key)) {
      writtenTiles.add(key);
      const store = await getStore();
      void store.put(key, bytes, Date.now()).catch(() => {});
      // store.put bypasses the cache's eviction; trim periodically.
      if (++writeCount % 100 === 0) {
        void getCache()
          .then((c) => c.evict(Date.now()))
          .catch(() => {});
      }
    }
    return bytes;
  } catch (err) {
    const store = await getStore();
    const hit = await store.get(key);
    if (hit) return hit.bytes;
    throw err;
  }
}

async function sha256Hex(input: string): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(input));
  return Array.from(new Uint8Array(buf))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

let apiOriginCache: string | null | undefined;
function apiOrigin(): string | null {
  if (apiOriginCache === undefined) {
    try {
      apiOriginCache = new URL(NATIVE_API_BASE).origin;
    } catch {
      apiOriginCache = null;
    }
  }
  return apiOriginCache;
}

// ANDROID-ONLY: CapacitorHttp's fetch shim corrupts ranged/206 binary, so call the plugin directly
// and decode ourselves. Bypassing fetch means adding the ngrok credential here and no mid-flight
// abort. Revisit if CapacitorHttp Android fixes ranged binary.
class NativeHttpRangeSource implements Source {
  private readonly url: string;
  constructor(url: string) {
    this.url = url;
  }
  getKey(): string {
    return this.url;
  }
  async getBytes(offset: number, length: number, signal?: AbortSignal): Promise<RangeResponse> {
    if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
    const headers: Record<string, string> = {
      Range: `bytes=${offset}-${offset + length - 1}`,
    };
    const origin = apiOrigin();
    if (NATIVE_NGROK_BASIC_AUTH && origin && this.url.startsWith(origin)) {
      headers.Authorization = `Basic ${btoa(NATIVE_NGROK_BASIC_AUTH)}`;
    }
    const res = await CapacitorHttp.request({
      url: this.url,
      method: "GET",
      headers,
      responseType: "arraybuffer",
    });
    if (res.status >= 400) {
      throw new Error(`pmtiles range HTTP ${res.status} for ${this.url}`);
    }
    let bytes = base64ToBytes(typeof res.data === "string" ? res.data : "");
    // A server/proxy that ignored Range returned the whole file; slice locally.
    if (res.status !== 206 && bytes.length > length) {
      bytes = bytes.slice(offset, offset + length);
    }
    return { data: bytes.buffer as ArrayBuffer };
  }
}

// Caches each (url, offset, length) range on disk.
class CachingSource implements Source {
  private readonly inner: Source;
  private readonly url: string;
  constructor(inner: Source, url: string) {
    this.inner = inner;
    this.url = url;
  }
  getKey(): string {
    return this.url;
  }
  async getBytes(
    offset: number,
    length: number,
    signal?: AbortSignal,
    etag?: string,
  ): Promise<RangeResponse> {
    const cache = await getCache();
    const key = await sha256Hex(`${this.url}:${offset}:${length}`);
    const bytes = await cache.fetchThrough(
      key,
      async () => {
        try {
          return new Uint8Array((await this.inner.getBytes(offset, length, signal, etag)).data);
        } catch (err) {
          console.error(
            "[tilecache] pmtiles range fetch failed:",
            this.url,
            `bytes=${offset}..${offset + length}`,
            err instanceof Error ? err.message : err,
          );
          throw err;
        }
      },
      Date.now(),
    );
    return { data: bytes.buffer as ArrayBuffer };
  }
}

const protocol = new Protocol();
const addedPmtiles = new Set<string>();
let registered = false;

const NATIVE_TILE_SCHEME = "nativetile";

// http(s) → nativetile://<kind>/<scheme>/<rest> (kind: r=raster, v=vector pbf).
function toNativeTileUrl(kind: "r" | "v", template: string): string {
  const m = /^(https?):\/\/(.*)$/.exec(template);
  return m ? `${NATIVE_TILE_SCHEME}://${kind}/${m[1]}/${m[2]}` : template;
}

const fetchBytes = (url: string, signal?: AbortSignal) => async (): Promise<Uint8Array> =>
  new Uint8Array(await (await fetch(url, signal ? { signal } : undefined)).arrayBuffer());

async function handleNativeTile(
  params: { url: string },
  abortController: AbortController,
): Promise<{ data: ImageBitmap | ArrayBuffer }> {
  const rest = params.url.slice(`${NATIVE_TILE_SCHEME}://`.length);
  const s1 = rest.indexOf("/");
  const kind = rest.slice(0, s1);
  const afterKind = rest.slice(s1 + 1);
  const s2 = afterKind.indexOf("/");
  const scheme = afterKind.slice(0, s2);
  const realUrl = `${scheme}://${afterKind.slice(s2 + 1)}`;

  const key = await sha256Hex(realUrl);
  const bytes = await networkFirstTile(key, fetchBytes(realUrl, abortController.signal));

  if (kind === "r") {
    return { data: await createImageBitmap(new Blob([bytes as BlobPart])) };
  }
  return { data: bytes.buffer as ArrayBuffer };
}

export const nativeTileCache: TileCacheAdapter = {
  registerMapProtocols(maplibregl: MapLibreModule) {
    if (registered) return;
    registered = true;
    maplibregl.addProtocol("pmtiles", protocol.tile.bind(protocol));
    maplibregl.addProtocol(NATIVE_TILE_SCHEME, handleNativeTile);
  },

  registerPmtiles(url: string) {
    if (addedPmtiles.has(url)) return;
    addedPmtiles.add(url);
    console.log("[tilecache] registerPmtiles:", url);
    const inner: Source =
      platformName() === "android" ? new NativeHttpRangeSource(url) : new FetchSource(url);
    protocol.add(new PMTiles(new CachingSource(inner, url)));
  },

  rasterTileUrl: (template) => toNativeTileUrl("r", template),

  vectorTileUrl: (template) => toNativeTileUrl("v", template),

  async warmRasterTile(url: string): Promise<void> {
    const key = await sha256Hex(url);
    await networkFirstTile(key, fetchBytes(url)).catch(() => null);
  },
};
