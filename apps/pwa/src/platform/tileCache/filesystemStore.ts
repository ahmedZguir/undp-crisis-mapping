// Native TileStore over @capacitor/filesystem. Keys are pre-hashed hex (one file each); mtime is the
// store time. DEVICE-VERIFY.

import { base64ToBytes, bytesToBase64 } from "../../lib/base64";
import type { StoredTile, TileStore } from "./cacheCore";

const DIR = "tilecache";

export async function createFilesystemTileStore(): Promise<TileStore> {
  const { Filesystem, Directory } = await import("@capacitor/filesystem");
  const directory = Directory.Cache;
  // mkdir on an existing dir logs a native error every launch (OS-PLUG-FILE-0010).
  try {
    await Filesystem.stat({ path: DIR, directory });
  } catch {
    try {
      await Filesystem.mkdir({ path: DIR, directory, recursive: true });
    } catch {
      // created concurrently / race
    }
  }
  const path = (key: string) => `${DIR}/${key}`;

  return {
    async get(key): Promise<StoredTile | null> {
      try {
        const res = await Filesystem.readFile({ path: path(key), directory });
        const st = await Filesystem.stat({ path: path(key), directory });
        const data = typeof res.data === "string" ? res.data : "";
        return {
          bytes: base64ToBytes(data),
          storedAt: typeof st.mtime === "number" ? st.mtime : 0,
        };
      } catch {
        return null;
      }
    },
    async put(key, bytes) {
      await Filesystem.writeFile({
        path: path(key),
        directory,
        data: bytesToBase64(bytes),
        recursive: true,
      });
    },
    async delete(key) {
      try {
        await Filesystem.deleteFile({ path: path(key), directory });
      } catch {
        // already gone
      }
    },
    async entries() {
      try {
        const res = await Filesystem.readdir({ path: DIR, directory });
        return res.files.map((f) => ({
          key: f.name,
          storedAt: typeof f.mtime === "number" ? f.mtime : 0,
        }));
      } catch {
        return [];
      }
    },
  };
}
