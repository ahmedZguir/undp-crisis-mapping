// Geolocation adapter. Native uses @capacitor/geolocation because navigator.geolocation is slow and
// sometimes refused inside a WebView. Errors carry a platform-neutral `code`.

import { isNativePlatform } from "../platformInfo";
import { nativeGeo } from "./native";
import { webGeo } from "./web";

export interface GeoCoords {
  latitude: number;
  longitude: number;
  accuracy: number;
}

export interface GeoOptions {
  enableHighAccuracy?: boolean;
  timeout?: number;
  maximumAge?: number;
}

export type GeoErrorCode = "unsupported" | "permission_denied" | "position_unavailable" | "timeout";

// "unknown" without the Permissions API.
export type GeoPermission = "granted" | "denied" | "prompt" | "unknown";

export class GeoError extends Error {
  code: GeoErrorCode;
  constructor(code: GeoErrorCode, message: string) {
    super(message);
    this.name = "GeoError";
    this.code = code;
  }
}

export interface GeoAdapter {
  isAvailable(): boolean;
  // Rejects with a `GeoError`.
  getCurrentPosition(opts?: GeoOptions): Promise<GeoCoords>;
  // Lets callers skip a request that can't resolve ("denied" won't re-prompt) and show the fallback.
  getPermissionState(): Promise<GeoPermission>;
}

export const geo: GeoAdapter = isNativePlatform() ? nativeGeo : webGeo;

// Coarse fix first (skips the GPS cold start), then a high-accuracy fix if at least as precise.
// Cancel when the user takes over so a late fix can't move things under them.
export function acquireProgressive(
  onFix: (coords: GeoCoords) => void,
  onError: (err: unknown) => void,
  opts: { maximumAge?: number } = {},
): () => void {
  let cancelled = false;
  geo
    .getCurrentPosition({ enableHighAccuracy: false, timeout: 6000, maximumAge: opts.maximumAge })
    .then((coarse) => {
      if (cancelled) return;
      onFix(coarse);
      // The adapter may fall back to coarse on a high-accuracy timeout.
      geo
        .getCurrentPosition({ enableHighAccuracy: true, timeout: 15000 })
        .then((precise) => {
          if (!cancelled && precise.accuracy <= coarse.accuracy) onFix(precise);
        })
        .catch(() => {
          // high-accuracy never arrived; keep the coarse fix
        });
    })
    .catch((err) => {
      if (!cancelled) onError(err);
    });
  return () => {
    cancelled = true;
  };
}
