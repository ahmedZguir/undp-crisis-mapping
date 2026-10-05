// Store-change notifications over a BroadcastChannel, keeping the SW outbox flush and the page in sync.

import { STORES_CHANNEL } from "../storeConfig";

type StoreChange = { kind: "drafts" } | { kind: "outbox" } | { kind: "drafts+outbox" };

let channel: BroadcastChannel | null = null;

function getChannel(): BroadcastChannel | null {
  if (channel) return channel;
  if (typeof BroadcastChannel === "undefined") return null;
  try {
    channel = new BroadcastChannel(STORES_CHANNEL);
  } catch {
    channel = null;
  }
  return channel;
}

export function publishChange(change: StoreChange): void {
  try {
    getChannel()?.postMessage(change);
  } catch {
    // best-effort
  }
}

// Opens its own channel: a BroadcastChannel never receives its own posts.
export function subscribeStoreChanges(
  kinds: StoreChange["kind"][] | "all",
  onChange: () => void,
): () => void {
  if (typeof BroadcastChannel === "undefined") return () => {};
  const ch = new BroadcastChannel(STORES_CHANNEL);
  ch.onmessage = (e) => {
    const kind = (e.data as StoreChange | undefined)?.kind;
    if (!kind) return;
    if (kinds === "all" || kinds.includes(kind)) onChange();
  };
  return () => ch.close();
}

export function resetChannel(): void {
  if (!channel) return;
  try {
    channel.close();
  } catch {
    // ignore
  }
  channel = null;
}
