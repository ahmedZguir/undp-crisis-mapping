import { type ExportFormat, exportCrisis } from "../api/admin";
import type { SearchRequest } from "../api/search";
import type { ExportScope } from "../types/admin";

// The data-only export can be slow on large crises, so it runs in a
// module-level store rather than the Export panel: the in-flight state survives
// inspector-tab switches and leaving the dashboard (AdminApp unmounts
// ReportsPage). One in-flight export per crisis.

export type CrisisExportState = {
  format: ExportFormat;
  downloading: boolean;
  error: string | null;
};

// e.g. "Turkey Earthquake 2026-06-21.csv". Built client-side because the API's
// Content-Disposition header isn't CORS-exposed. Non-ASCII names survive (the
// `download` attribute is UTF-8); only filename-illegal characters are stripped.
function exportFilename(crisisName: string | null, format: ExportFormat, now: Date): string {
  const cleaned = (crisisName ?? "")
    .replace(/[/\\:*?"<>|]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  const base = cleaned || "crisis-export";
  const p = (n: number) => String(n).padStart(2, "0");
  const stamp = `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())}`;
  return `${base} ${stamp}.${format}`;
}

const inflight = new Map<string, CrisisExportState>();
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

export function subscribeCrisisExport(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

// Stable reference between emits, so safe as a useSyncExternalStore snapshot.
export function getCrisisExport(crisisId: string): CrisisExportState | undefined {
  return inflight.get(crisisId);
}

export async function startCrisisExport(
  crisisId: string,
  format: ExportFormat,
  filter: SearchRequest,
  scope: ExportScope,
  crisisName: string | null,
): Promise<void> {
  if (inflight.get(crisisId)?.downloading) return;
  inflight.set(crisisId, { format, downloading: true, error: null });
  emit();
  try {
    const { blob } = await exportCrisis(crisisId, format, filter, scope);
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = exportFilename(crisisName, format, new Date());
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    inflight.delete(crisisId);
    emit();
  } catch (err) {
    inflight.set(crisisId, {
      format,
      downloading: false,
      error: err instanceof Error ? err.message : "Export failed",
    });
    emit();
  }
}
