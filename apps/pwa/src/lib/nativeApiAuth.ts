// Native-only: inject the ngrok Basic credential on API requests.
//
// Dormant: NATIVE_NGROK_BASIC_AUTH (src/config.ts) is empty, so this no-ops.
// Re-enabled by setting NATIVE_NGROK_BASIC_AUTH if the API goes back behind
// the ngrok gate.
//
// Why. The API sits behind a private ngrok gate (infra/ngrok-traffic-policy.yml)
// that demands `Authorization: Basic <creds>` on every request that doesn't
// already carry a `Bearer` JWT. The web app satisfies this transparently — the
// browser prompts once and auto-injects the cached credential. The native shell
// never loads a page on the ngrok origin, so nothing is cached and its naked
// API calls get 401'd at the edge. We replicate the browser's behaviour here:
// add the Basic header to requests aimed at the API origin that don't already
// set an Authorization header (so a logged-in coordinator's Bearer call is left
// untouched — the gate exempts those).
//
// This composes with `CapacitorHttp` (enabled in capacitor.config.ts): that
// plugin patches `window.fetch` to go through native HTTP and bypass CORS, and
// we wrap that patched fetch. The plugin patches during native bridge init,
// before this module's bundle runs, so the `window.fetch` we capture here is
// already the native one.
//
// Keep CapacitorHttp either way (native is always cross-origin, gate or not —
// it's what keeps CORS/preflight out of the picture).
//
// Imported for side effect (auto-installs on import) from main.tsx, ahead of
// any module that issues fetches (i18n backend, etc.).

import { NATIVE_API_BASE, NATIVE_NGROK_BASIC_AUTH } from "../config";
import { isNativePlatform } from "../platform/platformInfo";

let installed = false;

function installNativeApiAuth(): void {
  if (installed) return;
  if (!isNativePlatform()) return;
  if (!NATIVE_NGROK_BASIC_AUTH) return;
  if (typeof window === "undefined" || typeof window.fetch !== "function") return;

  let apiOrigin: string;
  try {
    apiOrigin = new URL(NATIVE_API_BASE).origin;
  } catch {
    // Misconfigured NATIVE_API_BASE — nothing to scope the credential to.
    return;
  }

  const basic = `Basic ${btoa(NATIVE_NGROK_BASIC_AUTH)}`;
  const nativeFetch = window.fetch.bind(window);

  window.fetch = (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;

    // Only the API origin gets the credential — never leak it to tile/CDN hosts.
    if (!url.startsWith(apiOrigin)) {
      return nativeFetch(input, init);
    }

    const headers = new Headers(
      init?.headers ?? (input instanceof Request ? input.headers : undefined),
    );
    // Leave an explicit Bearer (authenticated coordinator/admin call) alone:
    // the gate exempts Bearer requests and we must not overwrite the JWT.
    if (headers.has("Authorization")) {
      return nativeFetch(input, init);
    }
    headers.set("Authorization", basic);
    return nativeFetch(input, { ...init, headers });
  };

  installed = true;
}

installNativeApiAuth();
