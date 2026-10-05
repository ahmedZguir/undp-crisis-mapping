// iOS-only icon size override. The shared icon-only.png feeds BOTH the iOS
// AppIcon and the Android legacy launcher, so to size the wordmark differently
// on iOS we overwrite just the iOS AppIcon after @capacitor/assets has run.
// Gradient/font/sizes come from scripts/brand-assets.mjs.
//
// Run AFTER `npx @capacitor/assets generate --ios --android`.
import { PWA, WORDMARK_FRAC, sharp, svg } from "./brand-assets.mjs";

const APPICON = `${PWA}/ios/App/App/Assets.xcassets/AppIcon.appiconset/AppIcon-512@2x.png`;

await sharp(svg(1024, { widthFrac: WORDMARK_FRAC.ios }))
  .flatten()
  .png()
  .toFile(APPICON);
console.log(`iOS AppIcon overridden at widthFrac ${WORDMARK_FRAC.ios}`);
