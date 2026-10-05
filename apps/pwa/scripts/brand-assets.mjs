// Shared RASID brand primitives for the native/PWA asset scripts
// (gen-native-assets.mjs, fix-ios-icon.mjs, fix-android-adaptive.mjs).
// Import-only: no side effects.
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
// sharp ships as a dependency of @capacitor/assets; resolve it from there so we
// don't depend on it being hoisted to the top level under pnpm.
export const sharp = createRequire(require.resolve("@capacitor/assets/package.json"))("sharp");

export const PWA = resolve(dirname(fileURLToPath(import.meta.url)), "..");

// Gradient A · Faithful — light top-right (x1=100% y1=0%) to dark bottom-left.
export const GRADIENT = [
  ["0%", "#10b4ef"],
  ["45%", "#1180c8"],
  ["100%", "#0a4aa6"],
];
export const FONT = "Helvetica, Arial, sans-serif";

// Wordmark width as a fraction of the canvas, per output.
export const WORDMARK_FRAC = {
  android: 0.42, // Android/legacy launcher icon, Play Store icon, web manifest icons
  ios: 0.5, // iOS AppIcon, apple-touch-icon
  adaptiveFg: 0.4, // Android adaptive foreground (inside the safe zone)
  splash: 0.22,
};

// Build an SVG: full gradient (optional) + centered white RASID (optional).
export function svg(size, { gradient = true, text = true, widthFrac = 0.6 } = {}) {
  const stops = GRADIENT.map(([o, c]) => `<stop offset="${o}" stop-color="${c}"/>`).join("");
  const fs = Math.round(size * widthFrac * 0.42); // ~0.42 * target text width -> cap height
  const bg = gradient
    ? `<defs><linearGradient id="g" x1="100%" y1="0%" x2="0%" y2="100%">${stops}</linearGradient></defs>
       <rect width="${size}" height="${size}" fill="url(#g)"/>`
    : "";
  const word = text
    ? `<text x="${size / 2}" y="${size / 2 + fs * 0.35}" font-family="${FONT}" font-weight="bold"
            font-size="${fs}" fill="#ffffff" text-anchor="middle" letter-spacing="${size * 0.004}">RASID</text>`
    : "";
  return Buffer.from(
    `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}">${bg}${word}</svg>`,
  );
}
