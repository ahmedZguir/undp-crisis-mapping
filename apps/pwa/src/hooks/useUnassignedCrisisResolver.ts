import { type Dispatch, type SetStateAction, useCallback, useEffect, useState } from "react";
import { loadOutbox, patchOutboxPayload } from "../lib/db";
import { readCrisesCache } from "../lib/precache";
import { OUTBOX_NEEDS_CRISIS_EVENT, outbox } from "../platform/outbox";
import type { Crisis } from "../types";
import type { UseCrisisPersistence } from "./useCrisisPersistence";

// Count of outbox rows waiting for a crisis (CrisisAssignBanner) and its setter.
export function useUnassignedCrisisResolver(
  persistence: UseCrisisPersistence,
  saveCrisisAndSync: (crisis: Crisis) => void,
): [number, Dispatch<SetStateAction<number>>] {
  const [unassignedCount, setUnassignedCount] = useState(0);

  const assignCrisisToUnassigned = useCallback(async (crisisId: string) => {
    if (!crisisId) return;
    const rows = await loadOutbox();
    const targets = rows.filter((r) => !r.payload.crisis_id);
    await Promise.all(targets.map((r) => patchOutboxPayload(r.id, { crisis_id: crisisId })));
    setUnassignedCount(0);
    void outbox.requestFlush("crisis-assigned");
  }, []);

  useEffect(() => {
    const onNeedsCrisis = (e: Event) => {
      const detail = (e as CustomEvent<{ rowIds: string[] }>).detail;
      const count = detail?.rowIds?.length ?? 0;
      if (count === 0) return;
      const stored = persistence.stored;
      if (stored) {
        void assignCrisisToUnassigned(stored.id);
        return;
      }
      const cached = readCrisesCache() ?? [];
      if (cached.length === 1) {
        saveCrisisAndSync(cached[0]);
        void assignCrisisToUnassigned(cached[0].id);
        return;
      }
      setUnassignedCount(count);
    };
    window.addEventListener(OUTBOX_NEEDS_CRISIS_EVENT, onNeedsCrisis);
    return () => window.removeEventListener(OUTBOX_NEEDS_CRISIS_EVENT, onNeedsCrisis);
  }, [persistence.stored, saveCrisisAndSync, assignCrisisToUnassigned]);

  return [unassignedCount, setUnassignedCount];
}
