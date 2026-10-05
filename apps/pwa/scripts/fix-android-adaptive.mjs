import { writeFileSync } from "node:fs";
// Fixes the Android adaptive icon after `npx @capacitor/assets generate`:
//   1. The tool emits the foreground/background mipmaps at the legacy launcher
//      sizes (48–192px) instead of the 108dp adaptive sizes (108–432px); the
//      launcher then upscales them ~2.25x and the icon looks pixelated.
//   2. The tool's adaptive XML insets BOTH layers 16.7%, so the gradient
//      background doesn't bleed to the mask edge.
// This restores the 108dp sizes from assets/ and writes full-bleed XML
// (background fills the canvas; the wordmark foreground already sits in the
// safe zone, so no inset is needed).
//
// Run AFTER `npx @capacitor/assets generate --ios --android`.
import { PWA, sharp } from "./brand-assets.mjs";

const RES = `${PWA}/android/app/src/main/res`;
const FG = `${PWA}/assets/icon-foreground.png`;
const BG = `${PWA}/assets/icon-background.png`;

// 108dp adaptive icon at each density bucket.
const SIZES = { mdpi: 108, hdpi: 162, xhdpi: 216, xxhdpi: 324, xxxhdpi: 432 };

for (const [density, size] of Object.entries(SIZES)) {
  const dir = `${RES}/mipmap-${density}`;
  await sharp(FG).resize(size, size).png().toFile(`${dir}/ic_launcher_foreground.png`);
  await sharp(BG)
    .resize(size, size)
    .removeAlpha()
    .png()
    .toFile(`${dir}/ic_launcher_background.png`);
}

// Full-bleed adaptive XML: background fills the canvas (no inset), foreground
// wordmark is already inside the safe zone.
const xml = `<?xml version="1.0" encoding="utf-8"?>
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@mipmap/ic_launcher_background" />
    <foreground android:drawable="@mipmap/ic_launcher_foreground" />
</adaptive-icon>
`;
for (const name of ["ic_launcher.xml", "ic_launcher_round.xml"]) {
  writeFileSync(`${RES}/mipmap-anydpi-v26/${name}`, xml);
}

console.log("adaptive icon: 108dp layers + full-bleed XML written");
