import { useCallback, useState } from "react";
import { STORAGE_KEYS } from "../lib/storageKeys";
import type { Crisis } from "../types";

const STORAGE_KEY = STORAGE_KEYS.crisisPicked.key;

function readStored(): Crisis | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<Crisis> & {
      public_visibility?: Crisis["public_visibility"];
    };
    if (
      !parsed ||
      typeof parsed.id !== "string" ||
      typeof parsed.name !== "string" ||
      !("pmtiles_url" in parsed)
    ) {
      return null;
    }
    // Older stored crises lack `public_visibility`; default to `aggregate_view`.
    const hydrated: Crisis = {
      ...(parsed as Crisis),
      public_visibility: parsed.public_visibility ?? "aggregate_view",
    };
    return hydrated;
  } catch {
    return null;
  }
}

export interface UseCrisisPersistence {
  stored: Crisis | null;
  save: (crisis: Crisis) => void;
  clear: () => void;
}

export function useCrisisPersistence(): UseCrisisPersistence {
  const [stored, setStored] = useState<Crisis | null>(() => readStored());

  const save = useCallback((crisis: Crisis) => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(crisis));
    setStored(crisis);
  }, []);

  const clear = useCallback(() => {
    localStorage.removeItem(STORAGE_KEY);
    setStored(null);
  }, []);

  return { stored, save, clear };
}
