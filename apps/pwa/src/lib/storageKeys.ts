// Every Web Storage key the app uses; `storageKeys.test.ts` fails on a `rid-` literal elsewhere.
// "Delete my data" wipes by `kind`, so a new key is covered once it has the right one.
// Not here: IndexedDB and the idb-keyval `shared-photo` handoff.
// Don't rename a key without a migration: stored values would be orphaned.

/** Only `personal` (the citizen's reports, activity, location) is wiped by "Delete my data". */
export type StorageKind = "personal" | "preference" | "cache" | "admin";
export type StorageArea = "local" | "session";

interface ExactKey {
  readonly key: string;
  readonly kind: StorageKind;
  readonly area: StorageArea;
}

/** A family of keys `${prefix}${id}`, e.g. one per client or crisis. */
interface PrefixKey {
  readonly prefix: string;
  readonly kind: StorageKind;
  readonly area: StorageArea;
}

export type StorageKeyDef = ExactKey | PrefixKey;

export const STORAGE_KEYS = {
  // personal
  reportsCache: { prefix: "rid-reports-cache:", kind: "personal", area: "local" },
  reporterStats: { prefix: "rid-reporter-stats:", kind: "personal", area: "local" },
  reportNoticeSeen: { key: "rid-report-notice-seen", kind: "personal", area: "local" },
  // Not written any more; kept so "Delete my data" wipes values older builds left.
  lastKnownLocation: { key: "rid-last-known-location", kind: "personal", area: "local" },

  // preference
  theme: { key: "rid-theme", kind: "preference", area: "local" },
  langPicked: { key: "rid-lang-picked", kind: "preference", area: "local" },
  consentVersion: { key: "rid-consent-version", kind: "preference", area: "local" },
  consentAcceptedAt: { key: "rid-consent-accepted-at", kind: "preference", area: "local" },
  crisisPicked: { key: "rid-crisis-picked", kind: "preference", area: "local" },
  installMinimized: { key: "rid-install-minimized", kind: "preference", area: "local" },

  // cache
  crisesCache: { key: "rid-crises-cache", kind: "cache", area: "local" },
  precachedFor: { key: "rid-precached-for", kind: "cache", area: "local" },
  crisisStats: { prefix: "rid-crisis-stats:", kind: "cache", area: "local" },

  // admin
  adminDisplayTimezone: { key: "admin.displayTimezone", kind: "admin", area: "local" },
  adminFormPreview: { prefix: "formPreviewSchema:", kind: "admin", area: "local" },
  adminLastCrisis: { key: "rid-admin-last-crisis", kind: "admin", area: "session" },
  adminWorkspace: { prefix: "rid-admin-ws:", kind: "admin", area: "session" },
} as const satisfies Record<string, StorageKeyDef>;

// Window CustomEvent names announcing changes to the keys above.
export const STORAGE_EVENTS = {
  reportsCacheUpdated: "rid-reports-cache-updated",
  reportNoticeChanged: "rid-report-notice",
  resourceUpdated: "rid-resource-updated",
} as const;

export function keyFor(def: PrefixKey, id: string): string {
  return `${def.prefix}${id}`;
}

function matches(def: StorageKeyDef, key: string): boolean {
  return "key" in def ? def.key === key : key.startsWith(def.prefix);
}

/** Removes every stored key of the given kinds from `storage`. Throws on storage errors. */
export function removeKeysOfKind(
  kinds: readonly StorageKind[],
  area: StorageArea,
  storage: Storage,
): void {
  const defs = Object.values(STORAGE_KEYS).filter(
    (d: StorageKeyDef) => d.area === area && kinds.includes(d.kind),
  );
  const doomed: string[] = [];
  for (let i = 0; i < storage.length; i++) {
    const key = storage.key(i);
    if (key && defs.some((d) => matches(d, key))) doomed.push(key);
  }
  for (const key of doomed) storage.removeItem(key);
}
