import { useState } from "react";
import { useAdminTimezone } from "../../hooks/useAdminTimezone";
import { useAnalysisReports } from "../../hooks/useAnalysisReports";
import { formatDisplay } from "../../lib/adminTimezone";
import type {
  CrisisReportDetail,
  CrisisReportListItem,
  CrisisReportStatus,
} from "../../types/admin";

// Falls back to a generic verb so the spinner is never label-less.
function phaseLabel(phase: string | null): string {
  switch (phase) {
    case "analysing":
      return "Analysing…";
    case "summarising":
      return "Summarising…";
    case "rendering":
      return "Rendering…";
    case "uploading":
      return "Uploading…";
    default:
      return "Working…";
  }
}

// Coverage is already 0-100. Small nonzero values must not render as "0%".
function coverageLabel(pct: number | null): string | null {
  if (pct == null) return null;
  if (pct === 0) return "0%";
  if (pct > 0 && pct < 0.1) return "<0.1%";
  return `${pct < 10 ? pct.toFixed(1) : Math.round(pct)}%`;
}

function statusChipClass(status: CrisisReportStatus): string {
  return status === "succeeded" ? "chip safe" : status === "failed" ? "chip danger" : "chip blue";
}

// Opens a signed PDF URL. Signed URLs are short-lived, so callers re-fetch a
// fresh detail right before invoking this rather than trusting a stored URL.
function openPdf(url: string): void {
  window.open(url, "_blank", "noopener,noreferrer");
}

export function AnalysisReportsSection({ crisisId }: { crisisId: string }) {
  const tz = useAdminTimezone();
  const { list, listError, active, generating, generateError, generate, fetchDetail } =
    useAnalysisReports(crisisId);

  // Per-row "fetching fresh download URL" state, keyed by report id, plus a
  // shared error slot for download failures.
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const handleDownload = async (reportId: string) => {
    setDownloadError(null);
    setDownloadingId(reportId);
    try {
      const detail = await fetchDetail(reportId);
      if (detail.download_url) {
        openPdf(detail.download_url);
      } else {
        setDownloadError("This report has no downloadable file yet.");
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
      <div style={{ fontSize: 14, lineHeight: 1.5, color: "var(--c-ink-2)", marginBottom: 12 }}>
        A detailed PDF analysis covering the whole crisis at the moment you generate it, including
        AI-written findings and recommended actions. Your dashboard filters and map view don't
        apply. Generation runs in the background and takes a couple of minutes.
      </div>

      <button
        type="button"
        className="btn"
        style={{ width: "100%" }}
        onClick={() => void generate()}
        disabled={generating || activeRunning}
      >
        {generating || activeRunning ? (
          <>
            <span className="spin" /> {activeRunning ? phaseLabel(active.phase) : "Starting…"}
          </>
        ) : (
          "Generate analysis report"
        )}
      </button>

      {generateError && (
        <div style={{ fontSize: 14, color: "var(--c-danger)", marginTop: 8 }}>{generateError}</div>
      )}

      {active && <ActiveReportCard report={active} onDownload={handleDownload} />}

      {downloadError && (
        <div style={{ fontSize: 14, color: "var(--c-danger)", marginTop: 8 }}>{downloadError}</div>
      )}

      <div className="label-eyebrow" style={{ marginTop: 16, marginBottom: 6 }}>
        Past reports
      </div>
      {listError && (
        <div style={{ fontSize: 14, color: "var(--c-danger)" }}>
          Couldn't load history: {listError}
        </div>
      )}
      {list && list.length === 0 && (
        <div style={{ fontSize: 14, color: "var(--c-ink-3)" }}>
          No reports generated yet. Generate the first one above.
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

// The in-flight or just-finished report.
function ActiveReportCard({
  report,
  onDownload,
}: {
  report: CrisisReportDetail;
  onDownload: (reportId: string) => void;
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
        <span className="mono" style={{ fontSize: 14, fontWeight: 600 }}>
          {running ? phaseLabel(report.phase) : "Analysis report"}
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
          <ReportStatsLine
            reportCount={report.report_count}
            deviceCount={report.device_count}
            buildingCount={report.building_count}
            coveragePct={report.coverage_pct}
          />
          <button
            type="button"
            className="btn"
            style={{ width: "100%", marginTop: 8 }}
            onClick={() => onDownload(report.id)}
          >
            Download PDF
          </button>
        </>
      )}

      {report.status === "failed" && (
        <div
          style={{ fontSize: 14, color: "var(--c-danger)", marginTop: 6, wordBreak: "break-word" }}
        >
          {report.error ?? "Generation failed."}
          <div style={{ color: "var(--c-ink-3)", marginTop: 4 }}>
            Use “Generate analysis report” above to try again.
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
  row: CrisisReportListItem;
  tz: string;
  downloading: boolean;
  onDownload: (reportId: string) => void;
}) {
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
          {row.report_count != null ? ` · ${row.report_count.toLocaleString()} reports` : ""}
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

function ReportStatsLine({
  reportCount,
  deviceCount,
  buildingCount,
  coveragePct,
}: {
  reportCount: number | null;
  deviceCount: number | null;
  buildingCount: number | null;
  coveragePct: number | null;
}) {
  const parts: string[] = [];
  if (reportCount != null) parts.push(`${reportCount.toLocaleString()} reports`);
  if (deviceCount != null) parts.push(`${deviceCount.toLocaleString()} devices`);
  if (buildingCount != null) parts.push(`${buildingCount.toLocaleString()} buildings`);
  const coverage = coverageLabel(coveragePct);
  if (coverage != null) parts.push(`${coverage} coverage`);
  if (parts.length === 0) return null;
  return (
    <div className="mono" style={{ fontSize: 14, color: "var(--c-ink)", marginTop: 6 }}>
      {parts.join(" · ")}
    </div>
  );
}
