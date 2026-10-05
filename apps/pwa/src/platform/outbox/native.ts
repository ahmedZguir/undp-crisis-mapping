// Native outbox: same `lib/outbox.ts` flush over SQLite, but native triggers, since WebView
// 'online'/'visibilitychange' are unreliable and iOS WKWebView has no Background Sync.
// App alive: Capacitor network/resume/pause events. Suspended/killed: the `BackgroundSync` plugin
// (WorkManager / BGTaskScheduler), armed on enqueue and pause. Plugins are dynamically imported
// so they never enter the web bundle.

import { API_BASE } from "../../lib/apiBase";
import {
  clearBackoffTimer,
  enqueueFromDraft,
  flushAfterReconnect,
  requestFlush,
} from "../../lib/outbox";
import type { OutboxAdapter } from "./index";

let initialized = false;

// Best-effort: the OS may decline; in-app triggers cover the app-alive case.
async function scheduleBackgroundSync(): Promise<void> {
  try {
    const { BackgroundSync } = await import("./backgroundSyncPlugin");
    await BackgroundSync.schedule();
  } catch (err) {
    console.warn("[outbox] BackgroundSync.schedule failed:", err);
  }
}

async function registerTriggers(): Promise<void> {
  const [{ Network }, { App }, { BackgroundTask }, { BackgroundSync }] = await Promise.all([
    import("@capacitor/network"),
    import("@capacitor/app"),
    import("@capawesome/capacitor-background-task"),
    import("./backgroundSyncPlugin"),
  ]);

  // A cold background launch has no JS context to resolve the API base.
  try {
    await BackgroundSync.configure({ apiBaseUrl: API_BASE });
  } catch (err) {
    console.warn("[outbox] BackgroundSync.configure failed:", err);
  }

  void Network.addListener("networkStatusChange", (status) => {
    if (status.connected) void flushAfterReconnect("native-online");
  });

  void App.addListener("resume", () => {
    void requestFlush("native-resume");
  });

  // Drain in the OS-granted window before suspension (finish() must get the task id back),
  // and re-arm the background task for a reconnect after suspension.
  void App.addListener("pause", async () => {
    void scheduleBackgroundSync();
    const taskId = await BackgroundTask.beforeExit(async () => {
      try {
        await requestFlush("native-background");
      } finally {
        BackgroundTask.finish({ taskId });
      }
    });
  });

  void requestFlush("native-boot");
  void scheduleBackgroundSync();
}

export const nativeOutbox: OutboxAdapter = {
  async enqueueFromDraft(draftId, photoId, payload) {
    const result = await enqueueFromDraft(draftId, photoId, payload);
    void scheduleBackgroundSync();
    return result;
  },
  requestFlush,
  clearBackoffTimer,
  init() {
    if (initialized) return;
    initialized = true;
    void registerTriggers();
  },
};
