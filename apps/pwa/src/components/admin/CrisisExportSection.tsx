import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { ExportFormat } from "../../api/admin";
import type { SearchRequest } from "../../api/search";
import { useAdminTimezone } from "../../hooks/useAdminTimezone";
import { usePhotoExports } from "../../hooks/usePhotoExports";
import { formatDisplay } from "../../lib/adminTimezone";
import { readWorkspace, writeWorkspace } from "../../lib/adminWorkspace";
import { getCrisisExport, startCrisisExport, subscribeCrisisExport } from "../../lib/crisisExport";
import type {
  ExportScope,
  PhotoExportDetail,
  PhotoExportListItem,
  ReportExportStatus,
} from "../../types/admin";
import { Icon } from "./icons";

const EXPORT_FORMATS: { id: ExportFormat; label: string; sub: string }[] = [
  { id: "geojson", label: "GeoJSON", sub: "GIS" },
  { id: "csv", label: "CSV", sub: "spreadsheet" },
];

// Persisted export-form choices (per browser tab; see `lib/adminWorkspace.ts`).
const EXPORT_WORKSPACE_KEY = "export";

interface ExportWorkspace {
  format: ExportFormat;
  includeImages: boolean;
  scope: ExportScope;
}

function phaseLabel(phase: string | null): string {
  switch (phase) {
    case "resolving":
      return "Finding matching reports…";
    case "streaming":
      return "Bundling photos…";
    case "finalising":
      return "Finalising…";
    default:
      return "Working…";
  }
}

function statusChipClass(status: ReportExportStatus): string {
  if (status === "succeeded") return "chip safe";
  if (status === "failed") return "chip danger";
  if (status === "expired") return "chip"; // neutral: bundle gone, audit kept
  return "chip blue";
}

function formatBytes(n: number | null): string | null {
  if (n == null) return null;
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v < 10 ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
}

// Downloads a signed bundle-part URL (served with Content-Disposition) via a
// hidden iframe, not an anchor click: the browser allows one pending top-level
// navigation, so several anchor clicks cancel all but the last. Each iframe
// downloads independently and streams to disk, which matters for multi-GB parts.
// Signed URLs are short-lived, so callers re-fetch fresh detail first.
function downloadUrl(url: string): void {
  const frame = document.createElement("iframe");
  frame.style.display = "none";
  frame.src = url;
  document.body.appendChild(frame);
  // The attachment response never "loads" the frame; remove it once the
  // download has detached. 60s is generous headroom for the request to start.
  setTimeout(() => frame.remove(), 60_000);
}

// Gap between starts so the "allow multiple downloads" prompt resolves once.
// UX only, not correctness.
const PART_DOWNLOAD_GAP_MS = 300;

const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

export function CrisisExportSection({
  crisisId,
  crisisName,
  filterPayload,
  viewTotal,
  crisisTotal,
  hasActiveFilters,
}: {
  crisisId: string;
  crisisName: string | null;
  filterPayload: SearchRequest;
  viewTotal: number | null;
  crisisTotal: number | null;
  hasActiveFilters: boolean;
}) {
  // The form unmounts on tab/page switches, so its choices are restored from
  // per-tab storage. Read once through a ref.
  const exportSeedRef = useRef<ExportWorkspace | null>(null);
  if (exportSeedRef.current === null) {
    exportSeedRef.current = {
      format: "geojson",
      includeImages: false,
      // Default to the filtered view when the coordinator has filters applied,
      // else the whole crisis (the two coincide when nothing is filtered, except
      // whole-crisis also keeps non-geolocated reports).
      scope: hasActiveFilters ? "view" : "crisis",
      // Merge over the defaults so a partial/old blob can't seed undefined.
      ...readWorkspace<Partial<ExportWorkspace>>(EXPORT_WORKSPACE_KEY, {}),
    };
  }
  const exportSeed = exportSeedRef.current;
  const [format, setFormat] = useState<ExportFormat>(exportSeed.format);
  const [includeImages, setIncludeImages] = useState(exportSeed.includeImages);
  const [scope, setScope] = useState<ExportScope>(exportSeed.scope);

  useEffect(() => {
    writeWorkspace<ExportWorkspace>(EXPORT_WORKSPACE_KEY, { format, includeImages, scope });
  }, [format, includeImages, scope]);

  const count = scope === "crisis" ? crisisTotal : viewTotal;

  return (
    <div>
      <div style={{ fontSize: 14, lineHeight: 1.5, color: "var(--c-ink-2)", marginBottom: 12 }}>
        Download this crisis's reports as data, optionally bundled with the report photos. Choose
        whether to export the reports your current filters select or the whole crisis.
      </div>

      <Choice
        label="Scope"
        options={[
          { id: "view", label: "Filtered view", sub: "current filters" },
          { id: "crisis", label: "Whole crisis", sub: "every report" },
        ]}
        value={scope}
        onChange={setScope}
      />

      <div className="scope-strip" style={{ margin: "2px 0 14px" }}>
        <span className="d" aria-hidden="true" />
        {scope === "crisis" ? (
          <>
            Exports the whole crisis: <b className="num">{(crisisTotal ?? 0).toLocaleString()}</b>{" "}
            reports.
          </>
        ) : (
          <>
            Exports your current view: <b className="num">{(viewTotal ?? 0).toLocaleString()}</b>{" "}
            reports.
          </>
        )}
      </div>

      <Choice
        label="Format"
        options={EXPORT_FORMATS.map((f) => ({ id: f.id, label: f.label, sub: f.sub }))}
        value={format}
        onChange={setFormat}
      />

      <Choice
        label="Contents"
        options={[
          { id: "data", label: "Data only", sub: "one file" },
          { id: "images", label: "Data + photos", sub: "zip bundle" },
        ]}
        value={includeImages ? "images" : "data"}
        onChange={(v) => setIncludeImages(v === "images")}
      />

      {includeImages ? (
        <PhotoExportPanel
          crisisId={crisisId}
          filterPayload={filterPayload}
          scope={scope}
          format={format}
        />
      ) : (
        <DataExportButton
          crisisId={crisisId}
          crisisName={crisisName}
          filterPayload={filterPayload}
          scope={scope}
          format={format}
          count={count}
        />
      )}
    </div>
  );
}

// Labelled segmented choice using the `.choice` button look.
function Choice<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: { id: T; label: string; sub: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className="field" style={{ marginBottom: 14 }}>
      <span className="label-eyebrow">{label}</span>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: `repeat(${options.length}, 1fr)`,
          gap: 4,
          marginTop: 6,
        }}
      >
        {options.map((o) => {
          const selected = value === o.id;
          return (
            <button
              key={o.id}
              type="button"
              className={`choice${selected ? " selected" : ""}`}
              aria-pressed={selected}
              onClick={() => onChange(o.id)}
              style={{
                flexDirection: "column",
                gap: 2,
                padding: "10px 6px",
                justifyContent: "center",
              }}
            >
              <span style={{ fontSize: 13, fontWeight: 700 }}>{o.label}</span>
              <span style={{ fontSize: 10, color: "var(--c-ink-3)" }}>{o.sub}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

// Data-only download: streamed blob, fired through the module-level store so the
// in-flight state survives tab switches and navigation.
function DataExportButton({
  crisisId,
  crisisName,
  filterPayload,
  scope,
  format,
  count,
}: {
  crisisId: string;
  crisisName: string | null;
  filterPayload: SearchRequest;
  scope: ExportScope;
  format: ExportFormat;
  count: number | null;
}) {
  const exportState = useSyncExternalStore(subscribeCrisisExport, () => getCrisisExport(crisisId));
  const downloading = exportState?.downloading ?? false;
  const error = exportState?.error ?? null;
  const label = format === "geojson" ? "GeoJSON" : "CSV";

  return (
    <>
      <button
        type="button"
        className="btn"
        style={{
          width: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: 8,
        }}
        disabled={downloading}
        onClick={() => void startCrisisExport(crisisId, format, filterPayload, scope, crisisName)}
      >
        {downloading ? (
          <>
            <span className="spin" aria-hidden="true" style={{ width: 14, height: 14 }} />
            Preparing {label}…
          </>
        ) : (
          <>
            <Icon.download width="14" height="14" />
            Download {label}
            {count != null ? ` (${count.toLocaleString()})` : ""}
          </>
        )}
      </button>
      {error && <div style={{ fontSize: 14, color: "var(--c-danger)", marginTop: 8 }}>{error}</div>}
    </>
  );
}

// Data + photos: a background job that bundles photos into zip parts (start, poll, history, download).
function PhotoExportPanel({
  crisisId,
  filterPayload,
  scope,
  format,
}: {
  crisisId: string;
  filterPayload: SearchRequest;
  scope: ExportScope;
  format: ExportFormat;
}) {
  const tz = useAdminTimezone();
  const { list, listError, active, starting, startError, start, fetchDetail } =
    usePhotoExports(crisisId);

  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const handleDownload = async (exportId: string) => {
    setDownloadError(null);
    setDownloadingId(exportId);
    try {
      const detail = await fetchDetail(exportId);
      if (detail.parts.length > 0) {
        for (let i = 0; i < detail.parts.length; i++) {
          downloadUrl(detail.parts[i].download_url);
          // Space the starts so the multiple-downloads prompt resolves once.
          if (i < detail.parts.length - 1) await sleep(PART_DOWNLOAD_GAP_MS);
        }
      } else {
        setDownloadError("This bundle is no longer available to download.");
      }
    } catch (err) {
      setDownloadError(err instanceof Error ? err.message : String(err));
    } finally {
      setDownloadingId(null);
    }
  };

  const activeRunning = active?.status === "running";

  return (
    <div>
      <div style={{ fontSize: 13, lineHeight: 1.5, color: "var(--c-ink-3)", marginBottom: 10 }}>
        Builds a zip of the matching report photos plus a {format === "geojson" ? "GeoJSON" : "CSV"}{" "}
        manifest, in the background. Large crises split into several parts. Download links stay
        available for 48 hours.
      </div>

      <button
        type="button"
        className="btn"
        style={{ width: "100%" }}
        // Whole-crisis sends an empty filter (no spatial predicate, so
        // non-geolocated reports are included); view scope sends the live filter.
        // Same as `exportCrisis` in `api/admin.ts`.
        onClick={() =>
          void start({ filters: scope === "crisis" ? {} : filterPayload, scope, format })
        }
        disabled={starting || activeRunning}
      >
        {starting || activeRunning ? (
          <>
            <span className="spin" /> {activeRunning ? phaseLabel(active.phase) : "Starting…"}
          </>
        ) : (
          "Build photo bundle"
        )}
      </button>

      {startError && (
        <div style={{ fontSize: 14, color: "var(--c-danger)", marginTop: 8 }}>{startError}</div>
      )}

      {active && <ActiveExportCard report={active} onDownload={handleDownload} />}

      {downloadError && (
        <div style={{ fontSize: 14, color: "var(--c-danger)", marginTop: 8 }}>{downloadError}</div>
      )}

      <div className="label-eyebrow" style={{ marginTop: 16, marginBottom: 6 }}>
        Past bundles
      </div>
      {listError && (
        <div style={{ fontSize: 14, color: "var(--c-danger)" }}>
          Couldn't load history: {listError}
        </div>
      )}
      {list && list.length === 0 && (
        <div style={{ fontSize: 14, color: "var(--c-ink-3)" }}>
          No photo bundles yet. Build the first one above.
        </div>
      )}
      {list?.map((row) => (
        <HistoryRow
          key={row.id}
          row={row}
          tz={tz}
          downloading={downloadingId === row.id}
          onDownload={handleDownload}
        />
      ))}
    </div>
  );
}

function ActiveExportCard({
  report,
  onDownload,
}: {
  report: PhotoExportDetail;
  onDownload: (exportId: string) => void;
}) {
  const running = report.status === "running";
  const count = report.progress_count ?? null;
  const total = report.progress_total ?? null;
  const pct = running && count != null && total != null && total > 0 ? count / total : null;
  return (
    <div
      style={{
        marginTop: 12,
        padding: 10,
        borderRadius: 8,
        background: "var(--c-blue-50)",
        border: "1px solid var(--c-blue-200)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <span style={{ fontSize: 14, fontWeight: 600 }}>
          {running ? phaseLabel(report.phase) : "Photo bundle"}
        </span>
        <span
          className={statusChipClass(report.status)}
          style={{ padding: "4px 11px", fontSize: 14 }}
        >
          {running && <span className="spin" />}
          {report.status}
        </span>
      </div>

      {running && (
        <div style={{ marginTop: 8 }}>
          {count != null && total != null && total > 0 && (
            <div style={{ fontSize: 13, color: "var(--c-ink-2)", marginBottom: 4 }}>
              {count.toLocaleString()} of {total.toLocaleString()} photos
            </div>
          )}
          <div
            style={{
              height: 6,
              borderRadius: 999,
              background: "var(--c-blue-100)",
              overflow: "hidden",
            }}
          >
            {pct != null ? (
              <div
                style={{
                  width: `${Math.min(100, Math.max(2, pct * 100))}%`,
                  height: "100%",
                  background: "var(--c-blue-600)",
                  transition: "width 0.4s ease",
                }}
              />
            ) : (
              <div className="indeterminate-sweep" style={{ height: "100%" }} />
            )}
          </div>
        </div>
      )}

      {report.status === "succeeded" && (
        <>
          <BundleStatsLine photoCount={report.photo_count} totalBytes={report.total_bytes} />
          <button
            type="button"
            className="btn"
            style={{ width: "100%", marginTop: 8 }}
            onClick={() => onDownload(report.id)}
          >
            {report.parts.length > 1 ? `Download ${report.parts.length} parts` : "Download bundle"}
          </button>
        </>
      )}

      {report.status === "failed" && (
        <div
          style={{ fontSize: 14, color: "var(--c-danger)", marginTop: 6, wordBreak: "break-word" }}
        >
          {report.error ?? "Export failed."}
          <div style={{ color: "var(--c-ink-3)", marginTop: 4 }}>
            Use “Build photo bundle” above to try again.
          </div>
        </div>
      )}
    </div>
  );
}

function HistoryRow({
  row,
  tz,
  downloading,
  onDownload,
}: {
  row: PhotoExportListItem;
  tz: string;
  downloading: boolean;
  onDownload: (exportId: string) => void;
}) {
  const meta: string[] = [];
  if (row.format) meta.push(row.format === "geojson" ? "GeoJSON" : "CSV");
  if (row.scope) meta.push(row.scope === "crisis" ? "whole crisis" : "filtered view");
  if (row.photo_count != null) meta.push(`${row.photo_count.toLocaleString()} photos`);
  const size = formatBytes(row.total_bytes);
  if (size) meta.push(size);
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "8px 0",
        borderTop: "1px solid var(--c-line)",
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 14 }}>{formatDisplay(row.created_at, tz)}</span>
          <span
            className={statusChipClass(row.status)}
            style={{ padding: "2px 9px", fontSize: 13 }}
          >
            {row.status === "running" && <span className="spin" />}
            {row.status}
          </span>
        </div>
        <div style={{ fontSize: 13, color: "var(--c-ink-3)", marginTop: 2 }}>
          by {row.created_by_email || row.created_by || "—"}
          {meta.length > 0 ? ` · ${meta.join(" · ")}` : ""}
        </div>
      </div>
      {row.status === "succeeded" && row.download_ready && (
        <button
          type="button"
          className="btn secondary"
          style={{ flexShrink: 0 }}
          onClick={() => onDownload(row.id)}
          disabled={downloading}
        >
          {downloading ? "Preparing…" : "Download"}
        </button>
      )}
    </div>
  );
}

function BundleStatsLine({
  photoCount,
  totalBytes,
}: {
  photoCount: number | null;
  totalBytes: number | null;
}) {
  const parts: string[] = [];
  if (photoCount != null) parts.push(`${photoCount.toLocaleString()} photos`);
  const size = formatBytes(totalBytes);
  if (size) parts.push(size);
  if (parts.length === 0) return null;
  return (
    <div style={{ fontSize: 14, color: "var(--c-ink)", marginTop: 6 }}>{parts.join(" · ")}</div>
  );
}
