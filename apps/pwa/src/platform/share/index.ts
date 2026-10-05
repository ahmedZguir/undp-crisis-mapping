// Photo shared into the app. Web: Web Share Target via `sw-share-target.js`. Native: not yet
// supported; delegates to web, which is inert in the shell.

import { isNativePlatform } from "../platformInfo";
import { nativeShare } from "./native";
import { webShare } from "./web";

export interface ShareAdapter {
  // Consumed once.
  consumeSharedPhoto(): Promise<File | null>;
  isShareLaunch(search?: string): boolean;
}

export const share: ShareAdapter = isNativePlatform() ? nativeShare : webShare;
