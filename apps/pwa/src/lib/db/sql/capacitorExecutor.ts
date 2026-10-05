// Device `SqlExecutor` over `@capacitor-community/sqlite`, dynamically imported to stay out of the web
// bundle. DEVICE-VERIFY: untestable in jsdom; backend logic is tested on node:sqlite instead.

// Mirrored natively — keep in sync: the background workers
// (`OutboxSyncWorker.java`, `OutboxSync.swift`) open this database directly as
// `${DB_NAME}SQLite.db`.
import { LOCAL_DB_NAME as DB_NAME } from "../../storeConfig";
import type { SqlExecutor, SqlParams } from "./executor";

export async function getCapacitorExecutor(): Promise<SqlExecutor> {
  const { CapacitorSQLite, SQLiteConnection } = await import("@capacitor-community/sqlite");
  const sqlite = new SQLiteConnection(CapacitorSQLite);

  // Reuse across hot reloads / OTA bundle swaps to avoid "connection already exists".
  const exists = (await sqlite.isConnection(DB_NAME, false)).result;
  const conn = exists
    ? await sqlite.retrieveConnection(DB_NAME, false)
    : await sqlite.createConnection(DB_NAME, false, "no-encryption", 1, false);
  await conn.open();

  const params = (p?: SqlParams): unknown[] => (p ? [...p] : []);

  return {
    async run(sql, p) {
      // transaction=false: tx() manages atomicity; standalone runs autocommit.
      await conn.run(sql, params(p), false);
    },
    async all<T = Record<string, unknown>>(sql: string, p?: SqlParams): Promise<T[]> {
      const res = await conn.query(sql, params(p));
      return (res.values ?? []) as T[];
    },
    async get<T = Record<string, unknown>>(sql: string, p?: SqlParams): Promise<T | undefined> {
      const res = await conn.query(sql, params(p));
      return res.values?.[0] as T | undefined;
    },
    async tx<R>(fn: () => Promise<R>): Promise<R> {
      await conn.beginTransaction();
      try {
        const r = await fn();
        await conn.commitTransaction();
        return r;
      } catch (err) {
        try {
          await conn.rollbackTransaction();
        } catch {
          // already rolled back / connection lost
        }
        throw err;
      }
    },
    async close() {
      await conn.close();
      await sqlite.closeConnection(DB_NAME, false);
    },
  };
}
