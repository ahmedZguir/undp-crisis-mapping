/// <reference types="vitest/config" />
import path from "node:path";
import react from "@vitejs/plugin-react";
import { build as esbuild } from "esbuild";
import { type Plugin, defineConfig, loadEnv } from "vite";
import { VitePWA } from "vite-plugin-pwa";

const THEME = "#0468b1";
// Brand gradient midtone (same as Android `splash_background`), not THEME, so the browser splash
// flows into the in-page #boot-splash gradient without a colour jump.
const SPLASH_BG = "#1180c8";

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// Compiles src/lib/outbox-core.ts to a classic IIFE exposing `self.OutboxCore` for the SW's importScripts.
function swOutboxCorePlugin(): Plugin {
  const entry = path.resolve(process.cwd(), "src/lib/outbox-core.ts");
  async function compile(): Promise<string> {
    const result = await esbuild({
      entryPoints: [entry],
      bundle: true,
      format: "iife",
      globalName: "OutboxCore",
      platform: "browser",
      target: "es2020",
      write: false,
      legalComments: "none",
    });
    return result.outputFiles[0].text;
  }
  return {
    name: "undp-sw-outbox-core",
    configureServer(server) {
      server.middlewares.use("/sw-outbox-core.js", (_req, res) => {
        compile().then(
          (code) => {
            res.setHeader("Content-Type", "application/javascript");
            res.end(code);
          },
          (err) => {
            res.statusCode = 500;
            res.end(`/* outbox-core compile error: ${String(err)} */`);
          },
        );
      });
    },
    async generateBundle() {
      this.emitFile({
        type: "asset",
        fileName: "sw-outbox-core.js",
        source: await compile(),
      });
    },
  };
}

// Exposes the API base to the SW, which can't read VITE_API_BASE_URL at runtime.
function swApiConfigPlugin(apiBase: string): Plugin {
  const body = `self.UNDP_API_BASE = ${JSON.stringify(apiBase)};\n`;
  return {
    name: "undp-sw-api-config",
    configureServer(server) {
      server.middlewares.use("/sw-api-config.js", (_req, res) => {
        res.setHeader("Content-Type", "application/javascript");
        res.end(body);
      });
    },
    generateBundle() {
      this.emitFile({
        type: "asset",
        fileName: "sw-api-config.js",
        source: body,
      });
    },
  };
}

// The manifest is fetched without credentials by default, so behind the ngrok Basic gate it 401s and
// breaks installability. Harmless without the gate.
function manifestUseCredentialsPlugin(): Plugin {
  return {
    name: "undp-manifest-use-credentials",
    // Post-group and later in the array than VitePWA, so it runs after the link is injected.
    enforce: "post",
    transformIndexHtml: {
      order: "post",
      handler(html) {
        return html.replace(
          /<link rel="manifest"(?![^>]*crossorigin)([^>]*)>/,
          '<link rel="manifest"$1 crossorigin="use-credentials">',
        );
      },
    },
  };
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  // Mirrors lib/apiBase.ts's web logic — keep in sync. Native doesn't use the SW.
  const defaultApiBase = mode === "production" ? "/api" : "http://127.0.0.1:8000";
  const rawApiBase = (env.VITE_API_BASE_URL || defaultApiBase).replace(/\/$/, "");
  // Match on pathname so relative and absolute API bases both work.
  const apiPathPrefix = rawApiBase.startsWith("/")
    ? rawApiBase
    : new URL(rawApiBase).pathname.replace(/\/$/, "");
  const p = escapeRegex(apiPathPrefix);
  const apiBase = rawApiBase;

  const crisesUrlPattern = new RegExp(`^${p}/crises(\\?.*)?$`);
  const crisisStatsPattern = new RegExp(`^${p}/crises/[^/]+/stats(\\?.*)?$`);
  const heatTilesPattern = new RegExp(`^${p}/crises/[^/]+/tiles/heat/\\d+/\\d+/\\d+\\.pbf$`);
  const reportsPattern = new RegExp(`^${p}/reports(\\?.*)?$`);
  const meStatsPattern = new RegExp(`^${p}/me/stats(\\?.*)?$`);
  const pathMatcher =
    (re: RegExp) =>
    ({ url, sameOrigin }: { url: URL; sameOrigin: boolean }) =>
      sameOrigin && re.test(url.pathname);

  return {
    plugins: [
      react(),
      swApiConfigPlugin(apiBase),
      swOutboxCorePlugin(),
      VitePWA({
        registerType: "autoUpdate",
        // Registered only by useRegisterSW() on web: on native (capacitor://localhost) precache
        // fetches fail and assets are bundled anyway.
        injectRegister: false,
        workbox: {
          navigateFallback: "/index.html",
          // Same-origin backends must reach the network: otherwise window.open() on a signed URL
          // gets index.html instead of the file.
          navigateFallbackDenylist: [
            /^\/share-target$/,
            /^\/landing\.html$/,
            /^\/api\//,
            /^\/supabase\//,
          ],
          // Standalone landing page and its assets exceed the 2MB precache limit and aren't app shell.
          globIgnores: ["landing.html", "landing-assets/**"],
          importScripts: [
            "/sw-api-config.js",
            // Must precede sw-outbox.js, which uses `self.OutboxCore`.
            "/sw-outbox-core.js",
            "/sw-share-target.js",
            "/sw-pmtiles.js",
            "/sw-outbox.js",
          ],
          globPatterns: ["**/*.{js,css,html,svg,png,webp,webmanifest,json}"],
          runtimeCaching: [
            {
              urlPattern: pathMatcher(crisesUrlPattern),
              method: "GET",
              handler: "StaleWhileRevalidate",
              options: {
                cacheName: "crises-cache",
                expiration: {
                  // Crises shift often; this only absorbs flaky-network hiccups.
                  maxAgeSeconds: 5 * 60,
                  maxEntries: 8,
                },
                cacheableResponse: { statuses: [0, 200] },
              },
            },
            {
              urlPattern: pathMatcher(crisisStatsPattern),
              method: "GET",
              handler: "StaleWhileRevalidate",
              options: {
                cacheName: "crisis-stats-cache",
                expiration: { maxAgeSeconds: 10 * 60, maxEntries: 32 },
                cacheableResponse: { statuses: [0, 200] },
              },
            },
            {
              urlPattern: pathMatcher(heatTilesPattern),
              method: "GET",
              handler: "StaleWhileRevalidate",
              options: {
                cacheName: "heat-tiles-cache",
                expiration: { maxAgeSeconds: 24 * 60 * 60, maxEntries: 600 },
                cacheableResponse: { statuses: [0, 200] },
              },
            },
            {
              urlPattern: pathMatcher(reportsPattern),
              method: "GET",
              handler: "StaleWhileRevalidate",
              options: {
                cacheName: "reports-cache",
                expiration: { maxAgeSeconds: 60 * 60, maxEntries: 16 },
                cacheableResponse: { statuses: [0, 200] },
              },
            },
            {
              urlPattern: pathMatcher(meStatsPattern),
              method: "GET",
              handler: "StaleWhileRevalidate",
              options: {
                cacheName: "me-stats-cache",
                expiration: { maxAgeSeconds: 10 * 60, maxEntries: 8 },
                cacheableResponse: { statuses: [0, 200] },
              },
            },
            {
              urlPattern: /^https:\/\/tile\.openstreetmap\.org\/\d+\/\d+\/\d+\.png$/,
              method: "GET",
              handler: "CacheFirst",
              options: {
                cacheName: "osm-tiles-cache",
                // Effectively static; render previously browsed areas offline for weeks.
                expiration: { maxAgeSeconds: 30 * 24 * 60 * 60, maxEntries: 2000 },
                cacheableResponse: { statuses: [0, 200] },
              },
            },
          ],
        },
        includeAssets: ["favicon.svg", "apple-touch-icon.png", "icons.svg"],
        manifest: {
          name: "RASID",
          short_name: "RASID",
          description:
            "Community crisis-mapping platform: report damage, see what's happening near you.",
          lang: "en",
          dir: "ltr",
          start_url: "/",
          scope: "/",
          display: "standalone",
          theme_color: THEME,
          background_color: SPLASH_BG,
          share_target: {
            action: "/share-target",
            method: "POST",
            enctype: "multipart/form-data",
            params: {
              files: [{ name: "file", accept: ["image/*"] }],
            },
          },
          icons: [
            {
              src: "/icon-192.png",
              sizes: "192x192",
              type: "image/png",
              purpose: "any",
            },
            {
              src: "/icon-512.png",
              sizes: "512x512",
              type: "image/png",
              purpose: "any",
            },
            {
              src: "/icon-512-maskable.png",
              sizes: "512x512",
              type: "image/png",
              purpose: "maskable",
            },
          ],
        },
      }),
      manifestUseCredentialsPlugin(),
    ],
    preview: {
      // The tunnel host for manual testing; localhost always passes.
      allowedHosts: env.PWA_ALLOWED_HOST ? [env.PWA_ALLOWED_HOST] : [],
    },
    server: {
      allowedHosts: env.PWA_ALLOWED_HOST ? [env.PWA_ALLOWED_HOST] : [],
    },
    test: {
      environment: "jsdom",
      globals: true,
      setupFiles: ["./src/test/setup.ts"],
      restoreMocks: true,
      unstubGlobals: true,
      // Keep a dev `.env.local` API base from leaking into tests.
      env: { VITE_API_BASE_URL: "" },
    },
  };
});
