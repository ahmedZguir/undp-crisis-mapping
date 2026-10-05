// Client for the photo-resize Web Worker; falls back to the main thread if the Worker can't start.

import type { OutMessage } from "./photo-resize.worker";
import { type PhotoTier, defaultDeps, resizePhoto } from "./resizePhoto";

let workerInstance: Worker | null = null;
let nextId = 1;
const pending = new Map<number, { resolve: (blob: Blob) => void; reject: (err: Error) => void }>();

function getWorker(): Worker | null {
  if (workerInstance) return workerInstance;
  try {
    workerInstance = new Worker(new URL("./photo-resize.worker.ts", import.meta.url), {
      type: "module",
    });
    workerInstance.addEventListener("message", (event: MessageEvent<OutMessage>) => {
      const reply = event.data;
      const entry = pending.get(reply.id);
      pending.delete(reply.id);
      if (!entry) return;
      if (reply.ok) entry.resolve(reply.blob);
      else entry.reject(new Error(reply.error ?? "resize failed"));
    });
    return workerInstance;
  } catch {
    workerInstance = null;
    return null;
  }
}

export async function resizePhotoOffMainThread(
  blob: Blob,
  tier: PhotoTier = "baseline",
): Promise<Blob> {
  const worker = getWorker();
  if (!worker) {
    return resizePhoto(blob, defaultDeps, { tier });
  }
  return new Promise<Blob>((resolve, reject) => {
    const id = nextId++;
    pending.set(id, { resolve, reject });
    worker.postMessage({ id, blob, tier });
  });
}
