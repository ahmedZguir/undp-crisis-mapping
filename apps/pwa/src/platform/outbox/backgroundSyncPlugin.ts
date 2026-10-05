// Bridge to the native `BackgroundSync` plugin (WorkManager / BGTaskScheduler), which drains the
// SQLite outbox by POSTing `request_data` while the app is suspended or killed. On web the proxy rejects.
// CONTRACT: plugin name and methods must match the native BackgroundSyncPlugin (Java and Swift).

import { registerPlugin } from "@capacitor/core";

interface BackgroundSyncPlugin {
  // Persisted to SharedPreferences / UserDefaults for the worker.
  configure(options: { apiBaseUrl: string }): Promise<void>;
  // Idempotent.
  schedule(): Promise<void>;
  cancel(): Promise<void>;
}

export const BackgroundSync = registerPlugin<BackgroundSyncPlugin>("BackgroundSync");
