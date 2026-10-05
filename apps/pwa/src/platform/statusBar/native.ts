import { StatusBar, Style } from "@capacitor/status-bar";
import type { StatusBarAdapter, StatusBarTheme } from "./index";

let overlayEnabled = false;

export const nativeStatusBar: StatusBarAdapter = {
  apply(theme) {
    void configure(theme);
  },
};

async function configure(theme: StatusBarTheme): Promise<void> {
  try {
    // The header paints under the bar (it pads by env(safe-area-inset-top)); declaring this also
    // clears Android 15+'s default translucent scrim.
    if (!overlayEnabled) {
      await StatusBar.setOverlaysWebView({ overlay: true });
      overlayEnabled = true;
    }
    // Style names the *content* style, so this reads inverted but is correct.
    await StatusBar.setStyle({ style: theme === "dark" ? Style.Dark : Style.Light });
  } catch {
    // cosmetic, non-fatal
  }
}
