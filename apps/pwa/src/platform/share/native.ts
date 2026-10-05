// Placeholder until the Android intent filter / iOS Share Extension land (deferred post-v1).

import type { ShareAdapter } from "./index";
import { webShare } from "./web";

export const nativeShare: ShareAdapter = webShare;
