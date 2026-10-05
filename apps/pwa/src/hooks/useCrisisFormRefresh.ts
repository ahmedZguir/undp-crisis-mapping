import { useEffect } from "react";
import { fetchCrisisForm } from "../api/crisisForm";
import type { UseCrisisPersistence } from "./useCrisisPersistence";
import { useLatest } from "./useLatest";

export function useCrisisFormRefresh(
  persistence: UseCrisisPersistence,
  activeLocale: string,
): void {
  // Re-fetch the form on boot and on crisis/locale change: a new version may be published, and
  // generic-page strings are resolved server-side per locale. `persistence` is a fresh object each
  // render, so it's read through a ref to avoid looping the effect.
  const storedCrisisId = persistence.stored?.id;
  const persistenceRef = useLatest(persistence);
  useEffect(() => {
    if (!storedCrisisId) return;
    let cancelled = false;
    void (async () => {
      try {
        const form = await fetchCrisisForm(storedCrisisId, activeLocale);
        if (cancelled || !form) return;
        const cur = persistenceRef.current.stored;
        if (!cur || cur.id !== storedCrisisId) return;
        // Always overwrite: same version can still differ by locale.
        persistenceRef.current.save({ ...cur, ...form });
      } catch {
        // Offline: keep the cached schema.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [storedCrisisId, activeLocale]);
}
