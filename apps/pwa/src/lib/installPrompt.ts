import { STORAGE_KEYS } from "./storageKeys";
// Captures `beforeinstallprompt` at import time: Chrome fires it once per load, before React mounts.

interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

// Legacy key nothing writes; still cleared on install for devices that have it.
const MINIMIZED_KEY = STORAGE_KEYS.installMinimized.key;

let captured: BeforeInstallPromptEvent | null = null;
let installed = false;
const subscribers = new Set<() => void>();

function isStandalone(): boolean {
  if (typeof window === "undefined") return false;
  if (window.matchMedia?.("(display-mode: standalone)").matches) return true;
  // iOS Safari
  if ((window.navigator as unknown as { standalone?: boolean }).standalone) return true;
  return false;
}

function notify() {
  for (const cb of subscribers) cb();
}

if (typeof window !== "undefined") {
  window.addEventListener("beforeinstallprompt", (e) => {
    // No preventDefault(): keep the browser's own mini-infobar; the header button reuses the event.
    captured = e as BeforeInstallPromptEvent;
    notify();
  });
  window.addEventListener("appinstalled", () => {
    captured = null;
    installed = true;
    try {
      localStorage.removeItem(MINIMIZED_KEY);
    } catch {
      // ignore
    }
    notify();
  });
}

export function isInstallPromptAvailable(): boolean {
  if (installed) return false;
  if (isStandalone()) return false;
  return captured !== null;
}

export function subscribeInstallPrompt(cb: () => void): () => void {
  subscribers.add(cb);
  return () => {
    subscribers.delete(cb);
  };
}

export async function triggerInstallPrompt(): Promise<"accepted" | "dismissed" | "unavailable"> {
  if (!captured) return "unavailable";
  const ev = captured;
  await ev.prompt();
  const { outcome } = await ev.userChoice;
  captured = null;
  if (outcome === "accepted") {
    try {
      localStorage.removeItem(MINIMIZED_KEY);
    } catch {
      // ignore
    }
  }
  notify();
  return outcome;
}
