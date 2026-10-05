import { useEffect, useState } from "react";
import { getReportThumb } from "../lib/db";

// Local thumb as a blob: URL for offline My Reports. Only loads when `enabled`, so the online path
// does no IndexedDB read. Null when absent or loading.
export function useReportThumb(reportId: string, enabled: boolean): string | null {
  const [src, setSrc] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled) {
      setSrc(null);
      return;
    }
    let cancelled = false;
    let url: string | null = null;
    void getReportThumb(reportId).then((blob) => {
      if (cancelled || !blob) return;
      url = URL.createObjectURL(blob);
      setSrc(url);
    });
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [reportId, enabled]);

  return src;
}
