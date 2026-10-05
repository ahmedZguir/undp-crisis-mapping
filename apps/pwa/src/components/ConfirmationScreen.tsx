import { useTranslation } from "react-i18next";
import { useReporterStats } from "../hooks/useReporterStats";
import { ReporterProgress } from "./ReporterProgress";

interface ConfirmationScreenProps {
  reportId: string;
  onHome: () => void;
  onReportAnother: () => void;
  queued: boolean;
}

export function ConfirmationScreen({
  reportId,
  onHome,
  onReportAnother,
  queued,
}: ConfirmationScreenProps) {
  const { t } = useTranslation();
  const { stats } = useReporterStats();
  const confirmedAt = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const heading = queued
    ? t("confirmation.queuedHeading", { defaultValue: "Report queued" })
    : t("confirmation.heading", { defaultValue: "Report submitted" });
  const body = queued
    ? t("confirmation.queuedBody", {
        defaultValue: "We'll submit it automatically when your connection is back.",
      })
    : t("confirmation.body", { defaultValue: "Thanks! Your report is on its way." });

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "var(--c-surface)",
        overflowY: "auto",
        WebkitOverflowScrolling: "touch",
      }}
    >
      <div
        style={{
          padding: "24px 18px 18px",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          textAlign: "center",
          minHeight: "100%",
          boxSizing: "border-box",
        }}
      >
        <div
          style={{
            width: 76,
            height: 76,
            borderRadius: 999,
            background: queued ? "var(--c-blue-50)" : "var(--c-safe-bg)",
            color: queued ? "var(--c-blue-700)" : "var(--c-safe)",
            display: "grid",
            placeItems: "center",
            marginTop: 24,
            marginBottom: 16,
          }}
        >
          {queued ? (
            <svg width="36" height="36" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2.2" />
              <path
                d="M12 7v5l3 2"
                stroke="currentColor"
                strokeWidth="2.2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          ) : (
            <svg width="36" height="36" viewBox="0 0 36 36" fill="none" aria-hidden="true">
              <path
                d="M7 18L14 25L29 11"
                stroke="currentColor"
                strokeWidth="3"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          )}
        </div>

        <div style={{ fontSize: 24, fontWeight: 700, marginBottom: 6 }}>{heading}</div>
        <div style={{ fontSize: 13, color: "var(--c-ink-3)", maxWidth: 280, marginBottom: 18 }}>
          {body}
        </div>

        <div
          style={{
            width: "100%",
            textAlign: "start",
            padding: 12,
            marginBottom: 14,
            fontSize: 11,
            color: "var(--c-ink-3)",
            display: "grid",
            gridTemplateColumns: "90px 1fr",
            rowGap: 6,
            background: "var(--c-card)",
            border: "1px solid var(--c-line)",
            borderRadius: 10,
            fontFamily: "monospace",
            boxSizing: "border-box",
          }}
        >
          <span>{queued ? t("confirmation.submissionId") : t("confirmation.reportIdLabel")}</span>
          <span style={{ color: "var(--c-ink)", wordBreak: "break-all" }}>{reportId}</span>
          <span>{t("confirmation.captured")}</span>
          <span style={{ color: "var(--c-ink)" }}>{confirmedAt}</span>
        </div>

        <ReporterProgress stats={stats} />

        <button
          type="button"
          onClick={onHome}
          style={{
            width: "100%",
            height: 48,
            borderRadius: 12,
            border: "none",
            background: "var(--c-blue-700)",
            fontSize: 15,
            fontWeight: 600,
            color: "#fff",
            cursor: "pointer",
            marginTop: 14,
            marginBottom: 4,
          }}
        >
          {t("confirmation.backHome")}
        </button>
        <button
          type="button"
          onClick={onReportAnother}
          style={{
            width: "100%",
            height: 44,
            borderRadius: 12,
            border: "1.5px solid var(--c-line)",
            background: "transparent",
            fontSize: 13,
            fontWeight: 500,
            color: "var(--c-ink-2)",
            cursor: "pointer",
            padding: "10px 18px",
          }}
        >
          {t("confirmation.reportAnother")}
        </button>
      </div>
    </div>
  );
}
