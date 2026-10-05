// API base URL. Must stay same-site with the page so the SameSite=Lax refresh cookie is sent.
// Native WebViews serve from a local origin, so they need the absolute NATIVE_API_BASE.

import { NATIVE_API_BASE } from "../config";
import { isNativePlatform } from "../platform/platformInfo";

const DEFAULT_API_PORT = "8000";
// Stripped by Caddy before reaching FastAPI.
const WEB_API_PREFIX = "/api";

export function resolveApiBase(): string {
  if (isNativePlatform()) {
    const nativeBase = NATIVE_API_BASE.replace(/\/$/, "");
    if (nativeBase) {
      return nativeBase;
    }
    // Fail loud rather than resolve against the device's local origin.
    throw new Error(
      "NATIVE_API_BASE (src/config.ts) must be set for native (Capacitor) builds — " +
        "a relative API base cannot reach the server from the WebView's local origin.",
    );
  }

  // Escape hatch for pointing dev at a remote/LAN API; relative values resolve against the origin.
  const explicit = import.meta.env.VITE_API_BASE_URL;
  if (typeof explicit === "string" && explicit.length > 0) {
    const base = explicit.replace(/\/$/, "");
    if (base.startsWith("/") && typeof window !== "undefined" && window.location) {
      return window.location.origin + base;
    }
    return base;
  }

  if (import.meta.env.PROD && typeof window !== "undefined" && window.location) {
    return window.location.origin + WEB_API_PREFIX;
  }

  // Non-browser (workers, tests): keep module import safe.
  if (typeof window === "undefined" || !window.location) {
    return `http://127.0.0.1:${DEFAULT_API_PORT}`;
  }

  // Dev: same hostname as the page so localhost/127.0.0.1/LAN IP all stay same-site.
  const port = import.meta.env.VITE_API_PORT ?? DEFAULT_API_PORT;
  const protocol = window.location.protocol === "https:" ? "https:" : "http:";
  return `${protocol}//${window.location.hostname}:${port}`;
}

export const API_BASE = resolveApiBase();
