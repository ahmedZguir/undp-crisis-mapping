// Committed, non-secret build-time constants (distinct from .env and import.meta.env).

// Native only: the WebView's local origin can't reach a relative base. Web derives `/api` at runtime.
export const NATIVE_API_BASE = "https://rasid.qcri.org/api";

// "user:password" for the ngrok Basic gate (infra/ngrok-traffic-policy.yml). Empty = dormant. Native
// has no browser-cached credential, so lib/nativeApiAuth.ts injects it. Non-secret, throwaway.
export const NATIVE_NGROK_BASIC_AUTH = "";
