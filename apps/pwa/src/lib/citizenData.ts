// Web Storage half of "Delete my data"; IndexedDB is wiped by clearAllReports.

import { STORAGE_EVENTS, removeKeysOfKind } from "./storageKeys";

/** Removes every `personal` localStorage key. Best-effort. */
export function clearCitizenLocalData(storage?: Storage): void {
  try {
    removeKeysOfKind(["personal"], "local", storage ?? localStorage);
  } catch {
    return;
  }
  try {
    window.dispatchEvent(new CustomEvent(STORAGE_EVENTS.reportsCacheUpdated));
    window.dispatchEvent(new CustomEvent(STORAGE_EVENTS.reportNoticeChanged));
  } catch {
    // best-effort
  }
}
