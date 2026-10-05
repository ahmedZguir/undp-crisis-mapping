/* eslint-disable */
// SW fragment (workbox.importScripts): Background Sync flush of the outbox over raw IndexedDB.
// Decisions come from `self.OutboxCore` (compiled src/lib/outbox-core.ts, loaded first).
// IIFE because importScripts fragments share one global scope (e.g. each has a `DB_NAME`).
// Known limitation: an installed Android WebAPK blocks cleartext HTTP, so this fetch fails with
// "Failed to fetch" against an http:// API. Use HTTPS, or `adb reverse` + localhost on an emulator.

(() => {
  // Must match src/lib/storeConfig.ts — keep in sync (classic scripts can't import).
  const SYNC_TAG = "outbox-flush";
  const STORES_CHANNEL = "undp-stores";
  const DB_NAME = "undp-app";
  const core = self.OutboxCore;
  // From /sw-api-config.js. No fallback: silently POSTing to localhost on user devices is worse.
  const API_BASE = self.UNDP_API_BASE;

  // Don't nudge page clients: a woken background page wins the claim, fails its fetch and pushes
  // next_attempt_at into the future, so the SW then skips the row. The page has its own triggers.
  self.addEventListener("sync", (event) => {
    if (event.tag !== SYNC_TAG) return;
    console.log("[sw-outbox] sync event fired");
    event.waitUntil(swFlush());
  });

  function publish(change) {
    try {
      const ch = new BroadcastChannel(STORES_CHANNEL);
      ch.postMessage(change);
      ch.close();
    } catch (_e) {
      // best-effort
    }
  }

  function openDb() {
    // No version: pinning one failed silently with VersionError whenever the page bumped DB_VERSION.
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME);
      req.onerror = () => reject(req.error);
      req.onsuccess = () => resolve(req.result);
      req.onupgradeneeded = () => {
        // Fresh install: the page creates the real schema later.
      };
    });
  }

  function asPromise(req) {
    return new Promise((resolve, reject) => {
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  function txDone(t) {
    return new Promise((res) => {
      t.oncomplete = res;
    });
  }

  async function swFlush() {
    if (!core) {
      console.error("[sw-outbox] self.OutboxCore not loaded; skipping flush");
      return;
    }
    if (!API_BASE) {
      console.error("[sw-outbox] UNDP_API_BASE not configured; skipping flush");
      return;
    }
    let db;
    try {
      db = await openDb();
    } catch (_e) {
      return;
    }
    if (!db.objectStoreNames.contains("outbox") || !db.objectStoreNames.contains("photos")) {
      console.log("[sw-outbox] swFlush: outbox/photos store missing, skipping");
      db.close();
      return;
    }
    console.log("[sw-outbox] swFlush: starting, API_BASE=", API_BASE);
    try {
      await resetQueuedBackoff(db);
      const t = db.transaction("outbox", "readonly");
      const all = await asPromise(t.objectStore("outbox").getAll());
      const now = Date.now();
      const ready = core.selectReady(all, now);
      console.log(
        "[sw-outbox] swFlush: rows total=",
        all.length,
        "ready=",
        ready.length,
        "statuses=",
        all.map((r) => `${r.id.slice(0, 6)}:${r.status}@${r.next_attempt_at}`),
      );
      for (const candidate of ready) {
        const claimed = await claim(db, candidate.id);
        if (!claimed) {
          console.log("[sw-outbox] swFlush: claim lost for", candidate.id);
          continue;
        }
        console.log("[sw-outbox] swFlush: claimed", claimed.id, "submitting...");
        await submitOne(db, claimed);
      }
    } catch (err) {
      console.error("[sw-outbox] swFlush: threw", err);
      throw err;
    } finally {
      db.close();
    }
  }

  // Mirror of idbBackend.resetQueuedBackoff — keep in sync.
  async function resetQueuedBackoff(db) {
    const t = db.transaction("outbox", "readwrite");
    const store = t.objectStore("outbox");
    const all = await asPromise(store.getAll());
    const now = Date.now();
    let touched = 0;
    for (const row of all) {
      const reset = core.resetBackoffRow(row, now);
      if (!reset) continue;
      await asPromise(store.put(reset));
      touched += 1;
    }
    await txDone(t);
    if (touched > 0) publish({ kind: "outbox" });
  }

  // Mirror of idbBackend.claimOutboxRow — keep in sync.
  async function claim(db, id) {
    const t = db.transaction("outbox", "readwrite");
    const store = t.objectStore("outbox");
    const row = await asPromise(store.get(id));
    if (!row || !core.isReady(row, Date.now())) {
      return null;
    }
    const updated = { ...row, status: "submitting", updated_at: Date.now() };
    await asPromise(store.put(updated));
    await txDone(t);
    publish({ kind: "outbox" });
    return updated;
  }

  // Mirror of idbBackend.getPhoto — keep in sync.
  async function getPhoto(db, id) {
    const t = db.transaction("photos", "readonly");
    const row = await asPromise(t.objectStore("photos").get(id));
    if (!row) return null;
    // Pre-ArrayBuffer rows hold a `blob`.
    if (row.blob) return row.blob;
    return new Blob([row.data], { type: row.type });
  }

  async function submitOne(db, row) {
    // A referenced photo that can't be loaded is unrecoverable, so terminal.
    let blob = null;
    if (row.photo_id) {
      blob = await getPhoto(db, row.photo_id);
      if (!blob) {
        console.warn("[sw-outbox] submitOne: photo missing for", row.id, "photo_id=", row.photo_id);
        await markResult(db, row.id, {
          kind: "terminal",
          error: "photo missing from local storage",
        });
        return;
      }
    }
    const form = core.buildReportFormData(row.payload, blob);

    const url = `${API_BASE}/reports`;
    console.log(
      "[sw-outbox] submitOne: POST",
      url,
      "rowId=",
      row.id,
      "blobSize=",
      blob ? blob.size : 0,
    );
    let res;
    try {
      res = await fetch(url, { method: "POST", body: form });
    } catch (err) {
      console.error("[sw-outbox] submitOne: fetch threw", err);
      await markResult(db, row.id, { kind: "transient", error: String(err) });
      throw err; // make the browser retry the sync
    }
    console.log("[sw-outbox] submitOne: response", res.status, "for", row.id);
    if (res.ok) {
      await markResult(db, row.id, { kind: "success" });
      return;
    }
    const status = res.status;
    const kind = core.classifyStatus(status);
    await markResult(db, row.id, { kind, error: `submitReport failed: ${status}` });
    // Rethrow so the browser retries the sync.
    if (kind === "transient") throw new Error(`transient ${status}`);
  }

  // Mirror of idbBackend.markOutboxResult — keep in sync.
  async function markResult(db, id, result) {
    const t = db.transaction(["outbox", "photos"], "readwrite");
    const store = t.objectStore("outbox");
    const row = await asPromise(store.get(id));
    if (!row) return;
    const transition = core.nextOutboxState(row, result, {
      maxAttempts: core.MAX_OUTBOX_ATTEMPTS,
      backoff: core.backoffDelayMs,
      now: Date.now(),
    });
    if (transition.kind === "delete") {
      await asPromise(store.delete(id));
      if (row.photo_id) await asPromise(t.objectStore("photos").delete(row.photo_id));
    } else {
      await asPromise(store.put(transition.row));
    }
    await txDone(t);
    publish({ kind: "outbox" });
  }
})();
