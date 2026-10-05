import type { CapacitorConfig } from "@capacitor/cli";

// iOS + Android shell around the same `dist/`. Never change `appId`:
// every install would become a new one, losing queued outbox reports.
const config: CapacitorConfig = {
  appId: "com.moaminibrahim.undpcrisis",
  appName: "RASID",
  // Set back to "none" for normal use: bridge logs serialize full payloads on every plugin call.
  loggingBehavior: "debug",
  // Capgo OTA swaps a downloaded bundle over this one.
  webDir: "dist",
  server: {
    androidScheme: "https",
  },
  plugins: {
    // Native HTTP for fetch/XHR: the shell's local origin makes every API call cross-origin, and CORS
    // preflights (no Authorization) are 401'd by the ngrok gate.
    CapacitorHttp: {
      enabled: true,
    },
  },
};

export default config;
