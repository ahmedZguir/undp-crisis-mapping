/// <reference lib="webworker" />
import { errorMessage } from "./errors";
import { type PhotoTier, defaultDeps, resizePhoto } from "./resizePhoto";

interface InMessage {
  id: number;
  blob: Blob;
  tier?: PhotoTier;
}
interface OutSuccess {
  id: number;
  ok: true;
  blob: Blob;
}
interface OutError {
  id: number;
  ok: false;
  error: string;
}
export type OutMessage = OutSuccess | OutError;

self.addEventListener("message", async (event: MessageEvent<InMessage>) => {
  const { id, blob, tier } = event.data;
  try {
    const out = await resizePhoto(blob, defaultDeps, { tier });
    const reply: OutMessage = { id, ok: true, blob: out };
    (self as unknown as Worker).postMessage(reply);
  } catch (err) {
    const reply: OutMessage = {
      id,
      ok: false,
      error: errorMessage(err),
    };
    (self as unknown as Worker).postMessage(reply);
  }
});
