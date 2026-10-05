// Keeps the native status bar in step with the theme (no-op on web). Needed because targetSdk 36
// forces edge-to-edge and ignores theme attributes, leaving a dark translucent scrim otherwise.

import { isNativePlatform } from "../platformInfo";
import { nativeStatusBar } from "./native";
import { webStatusBar } from "./web";

// Not lib/theme's `Theme`, to avoid an import cycle (lib/theme is the caller).
export type StatusBarTheme = "light" | "dark";

export interface StatusBarAdapter {
  // Fire-and-forget; failures are swallowed.
  apply(theme: StatusBarTheme): void;
}

export const statusBar: StatusBarAdapter = isNativePlatform() ? nativeStatusBar : webStatusBar;
