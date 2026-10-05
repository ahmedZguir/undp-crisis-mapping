// Best-effort JSON read/write over Web Storage. Every storage failure (private
// mode, quota, disabled storage, unparseable value) is swallowed. Callers keep
// their own shape validation and change events.

/** Parsed JSON at `key`, or null if absent, empty, unreadable or unparseable. */
export function readJson<T>(key: string, storage?: Storage): T | null {
  try {
    const raw = (storage ?? localStorage).getItem(key);
    if (!raw) return null;
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

/** Stores `value` as JSON at `key`. Returns false if the write failed. */
export function writeJson(key: string, value: unknown, storage?: Storage): boolean {
  try {
    (storage ?? localStorage).setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}
