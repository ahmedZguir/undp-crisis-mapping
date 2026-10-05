/* eslint-disable */
// SW fragment: POST /share-target stores the shared photo in idb-keyval's DB, then redirects to
// /?share=1. IIFE because importScripts fragments share one global scope.

(() => {
  // Must match idb-keyval's defaults and SHARED_PHOTO_KEY in src/lib/shareTarget.ts — keep in sync.
  const KEYVAL_DB = "keyval-store";
  const KEYVAL_STORE = "keyval";
  const SHARED_PHOTO_KEY = "shared-photo";

  self.addEventListener("fetch", (event) => {
    const url = new URL(event.request.url);
    if (event.request.method !== "POST") return;
    if (url.pathname !== "/share-target") return;
    event.respondWith(handleShareTarget(event.request));
  });

  async function handleShareTarget(request) {
    try {
      const formData = await request.formData();
      const file = formData.get("file");
      if (file && typeof file === "object" && "arrayBuffer" in file) {
        await putSharedPhoto(file);
      }
    } catch (_err) {
      // The app will simply find no shared photo.
    }
    return Response.redirect("/?share=1", 303);
  }

  function putSharedPhoto(blob) {
    return new Promise((resolve, reject) => {
      const open = indexedDB.open(KEYVAL_DB, 1);
      open.onupgradeneeded = () => {
        open.result.createObjectStore(KEYVAL_STORE);
      };
      open.onerror = () => reject(open.error);
      open.onsuccess = () => {
        const db = open.result;
        const tx = db.transaction(KEYVAL_STORE, "readwrite");
        tx.objectStore(KEYVAL_STORE).put(blob, SHARED_PHOTO_KEY);
        tx.oncomplete = () => {
          db.close();
          resolve();
        };
        tx.onerror = () => {
          db.close();
          reject(tx.error);
        };
      };
    });
  }
})();
