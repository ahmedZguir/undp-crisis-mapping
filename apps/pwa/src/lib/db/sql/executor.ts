// Minimal SQL surface so the SQLite backend runs on node:sqlite in tests and the Capacitor plugin on device.

type SqlValue = string | number | null;
export type SqlParams = ReadonlyArray<SqlValue>;

export interface SqlExecutor {
  run(sql: string, params?: SqlParams): Promise<void>;
  all<T = Record<string, unknown>>(sql: string, params?: SqlParams): Promise<T[]>;
  get<T = Record<string, unknown>>(sql: string, params?: SqlParams): Promise<T | undefined>;
  // Commits on success, rolls back on throw.
  tx<R>(fn: () => Promise<R>): Promise<R>;
  close(): Promise<void>;
}
