// Anonymous per-install client id (UUIDv4) sent with every report. No fingerprinting, no
// identity link, never rotated (not even by "Delete all my reports"); only clearing site data
// mints a new one.

import { getMeta, setMeta } from "./db";

const META_KEY = "client_id";

let cached: string | null = null;

export async function getClientId(): Promise<string> {
  if (cached) return cached;
  const stored = await getMeta<string>(META_KEY);
  if (stored && typeof stored === "string") {
    cached = stored;
    return stored;
  }
  const minted = crypto.randomUUID();
  await setMeta(META_KEY, minted);
  cached = minted;
  return minted;
}

// Sync read for render-time cache hydration; null until `getClientId` has resolved (warm does it early).
export function peekClientId(): string | null {
  return cached;
}

export function _resetClientIdCacheForTests(): void {
  cached = null;
}
