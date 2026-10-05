// SW fragment: caches *.pmtiles byte-ranges in IndexedDB. Not the Cache API: it keys by URL only,
// and response.clone() breaks pmtiles parsing of cross-origin Range responses in some browsers.
// Invariant: NEVER clone or reconstruct the network response on a miss; cache via a deferred refetch
// (a concurrent one destabilised the original). IIFE because fragments share one global scope.

(() => {
  const DB_NAME = "pmtiles-cache";
  const DB_VERSION = 1;
  const STORE = "ranges";
  const MAX_ENTRIES = 1000;
  const REFETCH_DELAY_MS = 1500;

  // activeVersion (set by the page's SET_CRISIS_TILE_VERSION) keys cache hits and purges. Mobile
  // browsers kill idle SWs, losing it, so it's persisted to a separate "pmtiles-version" DB and
  // restored before the first fetch; otherwise offline lookups miss and buildings never appear.

  let activeVersion = "unknown";
  let versionRestored = false;

  function openVersionDb() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open("pmtiles-version", 1);
      req.onupgradeneeded = () => req.result.createObjectStore("kv");
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  async function persistVersion(v) {
    try {
      const db = await openVersionDb();
      await new Promise((resolve, reject) => {
        const t = db.transaction("kv", "readwrite");
        t.objectStore("kv").put(v, "activeVersion");
        t.oncomplete = () => {
          db.close();
          resolve();
        };
        t.onerror = () => {
          db.close();
          reject(t.error);
        };
      });
    } catch {
      // best-effort
    }
  }

  async function restoreVersion() {
    if (versionRestored) return;
    versionRestored = true;
    try {
      const db = await openVersionDb();
      const v = await new Promise((resolve, reject) => {
        const t = db.transaction("kv", "readonly");
        const req = t.objectStore("kv").get("activeVersion");
        req.onsuccess = () => {
          db.close();
          resolve(req.result);
        };
        req.onerror = () => {
          db.close();
          reject(req.error);
        };
      });
      if (v && typeof v === "string") activeVersion = v;
    } catch {
      // best-effort; leave activeVersion as "unknown"
    }
  }

  self.addEventListener("fetch", (event) => {
    const url = new URL(event.request.url);
    if (!url.pathname.endsWith(".pmtiles")) return;
    event.respondWith(handlePmtilesFetch(event.request));
  });

  self.addEventListener("message", (event) => {
    if (!event.data || event.data.type !== "SET_CRISIS_TILE_VERSION") return;
    const v = event.data.overture_release_pinned;
    activeVersion = v ? String(v) : "unknown";
    versionRestored = true; // page is authoritative
    void persistVersion(activeVersion);
    void purgeOtherVersions(activeVersion);
  });

  function openDb() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(STORE)) {
          const store = db.createObjectStore(STORE, { keyPath: "key" });
          store.createIndex("version", "version", { unique: false });
          store.createIndex("ts", "ts", { unique: false });
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  function rangesStore(db, mode) {
    return db.transaction(STORE, mode).objectStore(STORE);
  }

  function asPromise(req) {
    return new Promise((resolve, reject) => {
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  function entryKey(version, url, range) {
    return `${version}|${url}|${range || "full"}`;
  }

  async function idbGet(version, url, range) {
    const db = await openDb();
    try {
      return await asPromise(rangesStore(db, "readonly").get(entryKey(version, url, range)));
    } finally {
      db.close();
    }
  }

  async function idbPut(entry) {
    const db = await openDb();
    try {
      await asPromise(rangesStore(db, "readwrite").put(entry));
    } finally {
      db.close();
    }
  }

  async function idbCountAndTrim() {
    const db = await openDb();
    try {
      const store = rangesStore(db, "readwrite");
      const count = await asPromise(store.count());
      if (count <= MAX_ENTRIES) return;
      const overage = count - MAX_ENTRIES;
      const idx = store.index("ts");
      let removed = 0;
      await new Promise((resolve, reject) => {
        const cursorReq = idx.openCursor();
        cursorReq.onsuccess = () => {
          const cursor = cursorReq.result;
          if (!cursor || removed >= overage) return resolve();
          cursor.delete();
          removed += 1;
          cursor.continue();
        };
        cursorReq.onerror = () => reject(cursorReq.error);
      });
    } finally {
      db.close();
    }
  }

  async function purgeOtherVersions(keepVersion) {
    try {
      const db = await openDb();
      try {
        const store = rangesStore(db, "readwrite");
        await new Promise((resolve, reject) => {
          const cursorReq = store.openCursor();
          cursorReq.onsuccess = () => {
            const cursor = cursorReq.result;
            if (!cursor) return resolve();
            if (cursor.value.version !== keepVersion) cursor.delete();
            cursor.continue();
          };
          cursorReq.onerror = () => reject(cursorReq.error);
        });
      } finally {
        db.close();
      }
    } catch {
      // best-effort
    }
  }

  async function handlePmtilesFetch(request) {
    await restoreVersion();

    const range = request.headers.get("Range");
    const url = request.url;

    // Snapshot BEFORE any await: Chrome threw "Failed to fetch" reusing the FetchEvent's Request after one.
    const networkRequest = new Request(url, {
      method: "GET",
      headers: request.headers,
      mode: request.mode,
      credentials: request.credentials,
    });

    let hit = null;
    try {
      hit = await idbGet(activeVersion, url, range);
    } catch {
      hit = null;
    }
    if (hit?.body) {
      const headers = new Headers();
      if (hit.contentType) headers.set("content-type", hit.contentType);
      if (range) {
        return new Response(hit.body, {
          status: 206,
          statusText: "Partial Content",
          headers,
        });
      }
      return new Response(hit.body, { status: 200, headers });
    }

    // Miss: pure passthrough (see the invariant above).
    const response = await fetch(networkRequest);

    if (response.status === 206 || response.ok) {
      scheduleBackgroundCache(activeVersion, url, range, {
        headers: request.headers,
        mode: request.mode,
        credentials: request.credentials,
      });
    }

    return response;
  }

  // `init` carries the original request's { headers, mode, credentials }.
  function scheduleBackgroundCache(version, url, range, init) {
    setTimeout(() => {
      void backgroundCache(version, url, range, init);
    }, REFETCH_DELAY_MS);
  }

  async function backgroundCache(version, url, range, init) {
    try {
      const refetch = new Request(url, { method: "GET", ...init });
      const r = await fetch(refetch);
      if (!(r.status === 206 || r.ok)) return;
      const body = await r.arrayBuffer();
      const contentType = r.headers.get("content-type") || "";
      await idbPut({
        key: entryKey(version, url, range),
        version,
        url,
        range: range || "full",
        contentType,
        body,
        ts: Date.now(),
      });
      await idbCountAndTrim();
    } catch {
      // best-effort
    }
  }
})();
