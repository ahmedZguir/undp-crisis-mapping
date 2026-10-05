// Form-builder preview handoff: the builder writes `formPreviewSchema:<crisisId>` to localStorage
// and opens `?preview=1&crisis_id=<id>`. Not sessionStorage: `window.open()` gets a fresh one.
import type { FormSchema } from "../types";
import { STORAGE_KEYS, keyFor } from "./storageKeys";

function storageKey(crisisId: string): string {
  return keyFor(STORAGE_KEYS.adminFormPreview, crisisId);
}

export interface PreviewHandoff {
  crisisId: string;
  schema: FormSchema;
  version: number;
}

// Cached because the first read clears the entry; StrictMode's second render would otherwise get null.
const NOT_COMPUTED = Symbol("preview-handoff-not-computed");
let cached: PreviewHandoff | null | typeof NOT_COMPUTED = NOT_COMPUTED;

export function detectPreviewMode(): PreviewHandoff | null {
  if (cached !== NOT_COMPUTED) return cached;
  cached = computePreviewMode();
  return cached;
}

function computePreviewMode(): PreviewHandoff | null {
  if (typeof window === "undefined") return null;
  const params = new URLSearchParams(window.location.search);
  if (params.get("preview") !== "1") return null;
  const wantedCrisisId = params.get("crisis_id");
  if (!wantedCrisisId) return null;
  const key = storageKey(wantedCrisisId);
  const raw = window.localStorage.getItem(key);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as PreviewHandoff;
    if (parsed.crisisId !== wantedCrisisId) return null;
    if (!parsed.schema || typeof parsed.version !== "number") return null;
    // One-shot so a later non-preview launch doesn't pick up a stale snapshot.
    window.localStorage.removeItem(key);
    return parsed;
  } catch {
    window.localStorage.removeItem(key);
    return null;
  }
}
