// Theme persistence and DOM application. Defaults to light; never follows the OS preference.

import { statusBar } from "../platform/statusBar";
import { STORAGE_KEYS } from "./storageKeys";

export type Theme = "light" | "dark";

const STORAGE_KEY = STORAGE_KEYS.theme.key;

function safeStorage(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

function getStoredTheme(): Theme | null {
  const v = safeStorage()?.getItem(STORAGE_KEY);
  return v === "light" || v === "dark" ? v : null;
}

export function getActiveTheme(): Theme {
  return getStoredTheme() ?? "light";
}

export function applyTheme(theme: Theme): void {
  if (typeof document === "undefined") return;
  document.documentElement.dataset.theme = theme;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", theme === "dark" ? "#0a1220" : "#0468b1");
  statusBar.apply(theme);
}

export function setTheme(theme: Theme): void {
  safeStorage()?.setItem(STORAGE_KEY, theme);
  applyTheme(theme);
}

// Global source of truth (unlike useTheme()'s per-component state), for consumers CSS can't reach (map canvas).
export function getAppliedTheme(): Theme {
  if (typeof document === "undefined") return "light";
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

export function subscribeAppliedTheme(cb: (t: Theme) => void): () => void {
  if (typeof document === "undefined" || typeof MutationObserver === "undefined") {
    return () => {};
  }
  const obs = new MutationObserver(() => cb(getAppliedTheme()));
  obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  return () => obs.disconnect();
}
