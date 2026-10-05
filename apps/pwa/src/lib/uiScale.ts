// Publishes `--ui-scale` (0.8–1) from the visible viewport height so steps can shrink to fit.
// JS because CSS calc() can't divide lengths into a unitless ratio; visualViewport because iOS
// Safari's URL bar eats into 100vh.

// iPhone 11 Safari's visible area (~620–660px) lands near 0.85.
const MIN_H = 560;
const MAX_H = 820;
const MIN_SCALE = 0.8;
const MAX_SCALE = 1;

function computeScale(height: number): number {
  const t = Math.max(0, Math.min(1, (height - MIN_H) / (MAX_H - MIN_H)));
  return Math.round((MIN_SCALE + (MAX_SCALE - MIN_SCALE) * t) * 1000) / 1000;
}

function isEditableFocused(): boolean {
  const el = document.activeElement;
  if (!el) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || (el as HTMLElement).isContentEditable === true;
}

function apply(): void {
  if (typeof document === "undefined") return;
  // The soft keyboard shrinks visualViewport ~40%; freeze the scale while typing to avoid reflow.
  if (isEditableFocused()) return;
  const height = window.visualViewport?.height ?? window.innerHeight;
  document.documentElement.style.setProperty("--ui-scale", String(computeScale(height)));
}

export function initUiScale(): void {
  if (typeof window === "undefined") return;
  apply();
  window.addEventListener("resize", apply);
  window.addEventListener("orientationchange", apply);
  // Catches mobile Safari URL-bar show/hide, which plain resize misses.
  window.visualViewport?.addEventListener("resize", apply);
}
