// Must be first: on native, patches fetch with the ngrok Basic credential before
// any other module fetches.
import "./lib/nativeApiAuth";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.tsx";
import { getActiveTheme } from "./lib/theme";
import { initUiScale } from "./lib/uiScale";
import { statusBar } from "./platform/statusBar";
// Cairo for the Arabic UI (regular, medium, bold).
import "@fontsource/cairo/arabic-400.css";
import "@fontsource/cairo/arabic-600.css";
import "@fontsource/cairo/arabic-700.css";
import "./i18n";
// Attach beforeinstallprompt before React mounts so the one-shot event is not missed.
import "./lib/installPrompt";
import "./index.css";

// index.html never touches the native status bar and useTheme only re-applies on change,
// so without this Android keeps its default scrim until a theme toggle. No-op on web.
statusBar.apply(getActiveTheme());

// Set `--ui-scale` before first paint so steps render at the right size on short viewports.
initUiScale();

// biome-ignore lint/style/noNonNullAssertion: root element is guaranteed to exist in index.html
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

// Dismiss the boot splash after React paints (double rAF), but not before MIN_SPLASH_MS
// since navigation start so the wordmark does not just flash on warm loads.
const MIN_SPLASH_MS = 1200;
const bootSplash = document.getElementById("boot-splash");
if (bootSplash) {
  requestAnimationFrame(() =>
    requestAnimationFrame(() => {
      const remaining = Math.max(0, MIN_SPLASH_MS - performance.now());
      setTimeout(() => {
        bootSplash.style.opacity = "0";
        setTimeout(() => bootSplash.remove(), 300);
      }, remaining);
    }),
  );
}
