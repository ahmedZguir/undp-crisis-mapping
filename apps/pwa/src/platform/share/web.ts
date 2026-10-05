import { consumeSharedPhoto, isShareLaunch } from "../../lib/shareTarget";
import type { ShareAdapter } from "./index";

export const webShare: ShareAdapter = {
  consumeSharedPhoto,
  isShareLaunch,
};
