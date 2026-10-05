// Network conditions → PhotoTier.
// No `navigator.connection` (iOS Safari, Firefox) → baseline rather than penalize those users.

import type { PhotoTier } from "./resizePhoto";

interface NetworkInformationLike {
  effectiveType?: string;
  saveData?: boolean;
}

interface NavigatorWithConnection extends Navigator {
  connection?: NetworkInformationLike;
}

export function currentNetworkTier(): PhotoTier {
  if (typeof navigator === "undefined") return "baseline";
  const conn = (navigator as NavigatorWithConnection).connection;
  if (!conn) return "baseline";
  if (conn.saveData === true) return "constrained";
  if (conn.effectiveType === "slow-2g" || conn.effectiveType === "2g") return "constrained";
  return "baseline";
}
