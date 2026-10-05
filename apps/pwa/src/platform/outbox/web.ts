import { clearBackoffTimer, enqueueFromDraft, requestFlush } from "../../lib/outbox";
import type { OutboxAdapter } from "./index";

export const webOutbox: OutboxAdapter = {
  enqueueFromDraft,
  requestFlush,
  clearBackoffTimer,
  init() {
    // App.tsx registers the web flush triggers.
  },
};
