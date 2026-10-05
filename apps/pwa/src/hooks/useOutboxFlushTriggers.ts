import { useEffect } from "react";
import { pruneExpired } from "../lib/db";
import { onIdle } from "../lib/idle";
import { flushAfterReconnect } from "../lib/outbox";
import { outbox } from "../platform/outbox";

export function useOutboxFlushTriggers(): void {
  // Flush triggers.
  useEffect(() => {
    outbox.init();
    // Deferred off first paint.
    const bootFlush = onIdle(() => void outbox.requestFlush("boot"));
    // Draft / failed-row TTLs (storeConfig.ts). Queued rows never expire, so no flush race.
    const bootPrune = onIdle(() => {
      pruneExpired().catch((err) => console.warn("[outbox] pruneExpired failed", err));
    });
    const onOnline = () => void flushAfterReconnect("online");
    const onVisible = () => {
      if (document.visibilityState === "visible") void outbox.requestFlush("visible");
    };
    window.addEventListener("online", onOnline);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      bootFlush.cancel();
      bootPrune.cancel();
      window.removeEventListener("online", onOnline);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);
}
