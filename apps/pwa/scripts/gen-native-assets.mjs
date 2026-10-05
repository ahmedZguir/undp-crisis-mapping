import { mkdirSync } from "node:fs";
// RASID brand asset generator. Renders a full-bleed blue gradient (light
// top-right -> deep blue bottom-left, matching the original logo) with the white
// "RASID" wordmark as crisp vector text. No transparency hacks, no raster
// upscaling. Outputs:
//   - assets/*            @capacitor/assets sources (native icons + splash)
//   - store-assets/*      Play Store listing icon
//   - public/icon-*.png, public/apple-touch-icon.png   PWA / web manifest icons
//
// Brand spec (single source of truth: scripts/brand-assets.mjs):
//   - gradient "A · Faithful": #10b4ef -> #1180c8 -> #0a4aa6, 225deg
//   - wordmark: Helvetica/Arial Bold, white
//   - solid fallback colour (Android 12+ system splash): #1180c8 (gradient midtone)
//
// Full regeneration is four steps, in order:
//   1. node scripts/gen-native-assets.mjs            (this script — writes assets/)
//   2. npx @capacitor/assets generate --ios --android (resizes into every density;
//      --ios --android skips the tool's PWA webp output, which we don't use)
//   3. node scripts/fix-android-adaptive.mjs          (full-bleed adaptive icon:
//      108dp gradient background + safe-zone wordmark foreground + correct XML)
//   4. node scripts/fix-ios-icon.mjs                  (iOS-only wordmark size:
//      overrides just the iOS AppIcon; Android keeps this script's size)
import { PWA, WORDMARK_FRAC, sharp, svg } from "./brand-assets.mjs";

const OUT = `${PWA}/assets`;
const STORE = `${PWA}/store-assets`;
const PUB = `${PWA}/public`;
mkdirSync(OUT, { recursive: true });
mkdirSync(STORE, { recursive: true });

const opaque = (buf, file) => sharp(buf).flatten().png().toFile(`${OUT}/${file}`);
const keepAlpha = (buf, file) => sharp(buf).png().toFile(`${OUT}/${file}`);

// iOS + legacy Android square icon — full gradient + wordmark, opaque (no alpha).
await opaque(svg(1024, { widthFrac: WORDMARK_FRAC.android }), "icon-only.png");
// Android adaptive layers: background = full gradient (fills the 108dp canvas for
// a true full-bleed), foreground = wordmark only, sized inside the safe zone.
await opaque(svg(1024, { text: false }), "icon-background.png");
await keepAlpha(
  svg(1024, { gradient: false, widthFrac: WORDMARK_FRAC.adaptiveFg }),
  "icon-foreground.png",
);
// Splash — gradient + centered wordmark, full-bleed (same for light/dark).
await opaque(svg(2732, { widthFrac: WORDMARK_FRAC.splash }), "splash.png");
await opaque(svg(2732, { widthFrac: WORDMARK_FRAC.splash }), "splash-dark.png");

// Play Store listing icon (512x512, uploaded in the console — not in the APK).
await sharp(svg(512, { widthFrac: WORDMARK_FRAC.android }))
  .flatten()
  .png()
  .toFile(`${STORE}/play-store-icon-512.png`);

// PWA / web manifest icons (public/). Opaque, full-bleed gradient + RASID.
// Manifest icons mirror the Android size; apple-touch mirrors iOS.
const webOpaque = (size, wf, file) =>
  sharp(svg(size, { widthFrac: wf }))
    .flatten()
    .png()
    .toFile(`${PUB}/${file}`);
await webOpaque(192, WORDMARK_FRAC.android, "icon-192.png");
await webOpaque(512, WORDMARK_FRAC.android, "icon-512.png");
await webOpaque(512, WORDMARK_FRAC.android, "icon-512-maskable.png"); // RASID stays well within the maskable safe zone
await webOpaque(180, WORDMARK_FRAC.ios, "apple-touch-icon.png");

console.log("native asset sources (gradient + vector RASID) written to", OUT);
console.log("play-store-icon-512.png written to", STORE);
console.log("web manifest icons written to", PUB);
