import { useCallback, useEffect, useRef, useState } from "react";
import {
  type DraftRow,
  deleteDraft as dbDeleteDraft,
  getDraft as dbGetDraft,
  loadDrafts as dbLoadDrafts,
  saveDraft as dbSaveDraft,
  deletePhoto,
  getPhoto,
  putPhoto,
  requestPersistentStorageOnce,
} from "../lib/db";
import { subscribeStoreChanges } from "../lib/db/notify";
import { hasMaterialContent } from "../lib/draftMaterial";
import type { Crisis, FormSchema, FormState } from "../types";

const SAVE_DEBOUNCE_MS = 300;

type LoadedDraft = {
  state: FormState;
  step: number;
  schema?: FormSchema;
  version?: number;
  crisis?: Crisis;
};

interface UseDrafts {
  drafts: DraftRow[];
  newDraftId: () => string;
  save: (
    id: string,
    state: FormState,
    step: number,
    schema?: FormSchema,
    version?: number,
    crisis?: Crisis,
  ) => void;
  load: (id: string) => Promise<LoadedDraft | null>;
  discard: (id: string) => Promise<void>;
  // Call before promoting a draft so a stale autosave can't resurrect the row.
  cancelPending: (id: string) => Promise<void>;
}

interface PendingSave {
  id: string;
  state: FormState;
  step: number;
  schema?: FormSchema;
  version?: number;
  crisis?: Crisis;
  timeout: ReturnType<typeof setTimeout>;
}

// Session snapshot so a remounting screen shows the count without a 0 → N flash.
let draftsSnapshot: DraftRow[] = [];

export function useDrafts(): UseDrafts {
  const [drafts, setDrafts] = useState<DraftRow[]>(draftsSnapshot);
  const pendingRef = useRef<Map<string, PendingSave>>(new Map());
  const inFlightRef = useRef<Map<string, Promise<void>>>(new Map());
  // Permanent (ids are never reused): a mid-submit re-render must not resurrect a promoted draft.
  const tombstoneRef = useRef<Set<string>>(new Set());
  // Avoids rewriting the same photo Blob on every keystroke.
  const lastPhotoRef = useRef<Map<string, File | Blob | null>>(new Map());

  const refresh = useCallback(async () => {
    const all = await dbLoadDrafts();
    draftsSnapshot = all;
    setDrafts(all);
  }, []);

  useEffect(() => {
    void refresh();
    return subscribeStoreChanges(["drafts", "drafts+outbox"], () => void refresh());
  }, [refresh]);

  const flushPendingSave = useCallback(async (pending: PendingSave) => {
    const { id, state, step, schema, version, crisis } = pending;
    if (tombstoneRef.current.has(id)) return;

    const existing = await dbGetDraft(id);
    if (tombstoneRef.current.has(id)) return;

    if (!existing && !hasMaterialContent(state)) {
      return;
    }

    let photoId: string | null = existing?.photo_id ?? null;
    const lastPhoto = lastPhotoRef.current.get(id);
    if (state.photo && state.photo !== lastPhoto) {
      void requestPersistentStorageOnce();
      const newPhotoId = await putPhoto(state.photo);
      if (tombstoneRef.current.has(id)) {
        // Cancelled mid-flight: don't orphan the new photo.
        await deletePhoto(newPhotoId);
        return;
      }
      if (photoId) await deletePhoto(photoId);
      photoId = newPhotoId;
      lastPhotoRef.current.set(id, state.photo);
    } else if (!state.photo && photoId) {
      await deletePhoto(photoId);
      if (tombstoneRef.current.has(id)) return;
      photoId = null;
      lastPhotoRef.current.set(id, null);
    }

    if (tombstoneRef.current.has(id)) return;

    const { photo: _photo, ...rest } = state;
    const row: DraftRow = {
      id,
      state: rest,
      photo_id: photoId,
      step,
      updated_at: Date.now(),
      form_schema: schema ?? existing?.form_schema,
      form_version: version ?? existing?.form_version,
      crisis: crisis ?? existing?.crisis,
    };
    await dbSaveDraft(row);
  }, []);

  const save = useCallback(
    (
      id: string,
      state: FormState,
      step: number,
      schema?: FormSchema,
      version?: number,
      crisis?: Crisis,
    ) => {
      if (tombstoneRef.current.has(id)) return;
      const map = pendingRef.current;
      const prior = map.get(id);
      if (prior) clearTimeout(prior.timeout);
      const timeout = setTimeout(() => {
        const pending = map.get(id);
        if (!pending) return;
        map.delete(id);
        const promise = flushPendingSave(pending).catch(() => {
          // best-effort; next save will retry
        });
        inFlightRef.current.set(id, promise);
        void promise.finally(() => {
          if (inFlightRef.current.get(id) === promise) inFlightRef.current.delete(id);
        });
      }, SAVE_DEBOUNCE_MS);
      map.set(id, { id, state, step, schema, version, crisis, timeout });
    },
    [flushPendingSave],
  );

  const load = useCallback(async (id: string): Promise<LoadedDraft | null> => {
    const row = await dbGetDraft(id);
    if (!row) return null;
    let photo: File | null = null;
    if (row.photo_id) {
      const blob = await getPhoto(row.photo_id);
      if (blob) {
        photo =
          blob instanceof File
            ? blob
            : new File([blob], "photo", {
                type: blob.type || "application/octet-stream",
              });
        lastPhotoRef.current.set(id, photo);
      }
    }
    return {
      state: { ...row.state, photo },
      step: row.step,
      schema: row.form_schema,
      version: row.form_version,
      crisis: row.crisis,
    };
  }, []);

  const cancelPending = useCallback(async (id: string) => {
    tombstoneRef.current.add(id);
    const map = pendingRef.current;
    const pending = map.get(id);
    if (pending) {
      clearTimeout(pending.timeout);
      map.delete(id);
    }
    const inFlight = inFlightRef.current.get(id);
    if (inFlight) {
      try {
        await inFlight;
      } catch {
        // not user-actionable
      }
    }
  }, []);

  const discard = useCallback(
    async (id: string) => {
      await cancelPending(id);
      lastPhotoRef.current.delete(id);
      await dbDeleteDraft(id);
    },
    [cancelPending],
  );

  const newDraftId = useCallback(() => crypto.randomUUID(), []);

  return {
    drafts,
    newDraftId,
    save,
    load,
    discard,
    cancelPending,
  };
}
