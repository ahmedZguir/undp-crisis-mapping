import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { beforeEach, describe, expect, it } from "vitest";
import { STORAGE_EVENTS, STORAGE_KEYS, type StorageKeyDef, removeKeysOfKind } from "./storageKeys";

const SRC = path.resolve(process.cwd(), "src");
const REGISTRY = path.join(SRC, "lib", "storageKeys.ts");

function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) return sourceFiles(full);
    return /\.(ts|tsx)$/.test(e.name) && !/\.test\.tsx?$/.test(e.name) ? [full] : [];
  });
}

const defs: [string, StorageKeyDef][] = Object.entries(STORAGE_KEYS);
const name = (d: StorageKeyDef) => ("key" in d ? d.key : d.prefix);

describe("storage key registry", () => {
  it("every key and event name lives in storageKeys.ts", () => {
    // A `rid-` literal, or a raw string passed straight to Web Storage, means a key that bypassed
    // the registry and so has no kind: "Delete my data" would not know about it.
    const offenders: string[] = [];
    for (const file of sourceFiles(SRC)) {
      if (file === REGISTRY) continue;
      const text = readFileSync(file, "utf8");
      text.split("\n").forEach((line, i) => {
        if (
          /["'`]rid-/.test(line) ||
          /(local|session)Storage\.(get|set|remove)Item\(\s*["'`]/.test(line)
        ) {
          offenders.push(`${path.relative(SRC, file)}:${i + 1}: ${line.trim()}`);
        }
      });
    }
    expect(offenders).toEqual([]);
  });

  it("names are unique and no entry shadows another", () => {
    const names = defs.map(([, d]) => name(d));
    expect(new Set(names).size).toBe(names.length);
    for (const [a, da] of defs) {
      for (const [b, db] of defs) {
        if (a === b || "key" in db) continue;
        // A prefix family must not swallow another entry's keys.
        expect(name(da).startsWith(db.prefix), `${a} is inside ${b}'s prefix`).toBe(false);
      }
    }
  });

  it("event names don't collide with key names", () => {
    const names = new Set(defs.map(([, d]) => name(d)));
    for (const ev of Object.values(STORAGE_EVENTS)) expect(names.has(ev)).toBe(false);
  });
});

describe("removeKeysOfKind", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });

  it("removes exact and prefixed keys of the kind, only in the given area", () => {
    localStorage.setItem(STORAGE_KEYS.reportNoticeSeen.key, "{}");
    localStorage.setItem(`${STORAGE_KEYS.reportsCache.prefix}client-a`, "{}");
    localStorage.setItem(STORAGE_KEYS.theme.key, "dark");
    sessionStorage.setItem(STORAGE_KEYS.adminLastCrisis.key, "c1");

    removeKeysOfKind(["personal"], "local", localStorage);

    expect(Object.keys(localStorage)).toEqual([STORAGE_KEYS.theme.key]);
    expect(sessionStorage.getItem(STORAGE_KEYS.adminLastCrisis.key)).toBe("c1");
  });

  it("leaves keys it doesn't know about", () => {
    localStorage.setItem("some-library-key", "x");
    removeKeysOfKind(["personal", "preference", "cache", "admin"], "local", localStorage);
    expect(localStorage.getItem("some-library-key")).toBe("x");
  });
});
