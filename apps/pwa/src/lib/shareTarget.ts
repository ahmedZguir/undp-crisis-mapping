// Reads the photo the share-target SW handler stashed under `shared-photo`,
// converts it to a File, and clears the IDB entry so it's consumed exactly
// once. Owns its own IDB key, separate from the report-draft key.

import { del, get } from "idb-keyval";

const SHARED_PHOTO_KEY = "shared-photo";

export async function consumeSharedPhoto(): Promise<File | null> {
  const stored = await get<Blob | File>(SHARED_PHOTO_KEY);
  if (!stored) return null;
  await del(SHARED_PHOTO_KEY);
  if (stored instanceof File) return stored;
  return new File([stored], "shared.jpg", { type: stored.type || "image/jpeg" });
}

export function isShareLaunch(search: string = window.location.search): boolean {
  return new URLSearchParams(search).get("share") === "1";
}
