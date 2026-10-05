// Privacy-consent gate. Kept in sync localStorage (not getMeta) because the first-render gate
// decision must be synchronous.

import { langStore } from "./langGate";
import { STORAGE_KEYS } from "./storageKeys";

// Bump to force re-consent. Keep in sync with `policy_version` in docs/legal/privacy-policy.md.
export const POLICY_VERSION = 1;

const VERSION_KEY = STORAGE_KEYS.consentVersion.key;
const ACCEPTED_AT_KEY = STORAGE_KEYS.consentAcceptedAt.key;

function consentedVersion(): number {
  const raw = langStore().getItem(VERSION_KEY);
  if (!raw) return 0;
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) ? parsed : 0;
}

export function hasConsented(): boolean {
  return consentedVersion() >= POLICY_VERSION;
}

export function recordConsent(): void {
  langStore().setItem(VERSION_KEY, String(POLICY_VERSION));
  langStore().setItem(ACCEPTED_AT_KEY, new Date().toISOString());
}
