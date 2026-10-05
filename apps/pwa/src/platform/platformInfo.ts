// Platform detection. Code outside `src/platform/*` must use this, never Capacitor directly.
// `@capacitor/core` is safe on web: with no native runtime it reports "web".

import { Capacitor } from "@capacitor/core";

type PlatformName = "web" | "ios" | "android";

export function isNativePlatform(): boolean {
  return Capacitor.isNativePlatform();
}

export function platformName(): PlatformName {
  return Capacitor.getPlatform() as PlatformName;
}
