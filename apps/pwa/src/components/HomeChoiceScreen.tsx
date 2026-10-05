import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { crisisStatsKey, getCrisisStats } from "../api/reports";
import { useCrisisPersistence } from "../hooks/useCrisisPersistence";
import { useCachedResource } from "../lib/cachedResource";
import type { DraftRow } from "../lib/db";
import {
  isInstallPromptAvailable,
  subscribeInstallPrompt,
  triggerInstallPrompt,
} from "../lib/installPrompt";
import { ArrowRightIcon } from "./icons";

interface HomeChoiceScreenProps {
  onReport: () => void;
  onMap: () => void;
  onCrisis: () => void;
  drafts: DraftRow[];
  onContinueDraft: (id: string) => void;
  onViewAllDrafts: () => void;
}

export function HomeChoiceScreen({
  onReport,
  onMap,
  onCrisis,
  drafts,
  onContinueDraft,
  onViewAllDrafts,
}: HomeChoiceScreenProps) {
  const { stored } = useCrisisPersistence();
  const { t } = useTranslation();
  const [canInstall, setCanInstall] = useState(() => isInstallPromptAvailable());

  useEffect(() => {
    const refresh = () => setCanInstall(isInstallPromptAvailable());
    refresh();
    return subscribeInstallPrompt(refresh);
  }, []);
  const hasCrisis = Boolean(stored);
  const crisisName = stored?.name ?? "—";
  const crisisId = stored?.id ?? null;
  const hasDrafts = drafts.length > 0;

  // Synchronous from the warm cache so "new today" does not flash zero.
  const { data: stats } = useCachedResource(crisisId ? crisisStatsKey(crisisId) : null, () =>
    getCrisisStats(crisisId as string),
  );
  const last24h = stats?.last_24h ?? null;

  // One draft → resume it directly; several → let the user pick on the drafts page.
  const handleContinueDraft = () => {
    if (drafts.length === 0) return;
    if (drafts.length === 1) {
      onContinueDraft(drafts[0].id);
    } else {
      onViewAllDrafts();
    }
  };
  return (
    <div
      style={{
        minHeight: "100svh",
        display: "flex",
        flexDirection: "column",
        background: "var(--c-surface)",
        fontFamily: "var(--font-ui)",
        color: "var(--c-ink)",
      }}
    >
      <div
        style={{
          padding:
            "max(env(safe-area-inset-top, 12px), 12px) calc(16px + env(safe-area-inset-right)) 10px calc(16px + env(safe-area-inset-left))",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "var(--c-card)",
          position: "relative",
        }}
      >
        {canInstall && (
          <button
            type="button"
            onClick={() => void triggerInstallPrompt()}
            aria-label={t("sw.installPrompt", "Install this app")}
            title={t("sw.installPrompt", "Install this app")}
            style={{
              position: "absolute",
              insetInlineStart: "calc(16px + env(safe-area-inset-left))",
              top: "50%",
              transform: "translateY(-50%)",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 6,
              height: 34,
              padding: "0 12px",
              borderRadius: 999,
              border: 0,
              background: "var(--c-blue-700)",
              color: "#fff",
              fontSize: 12,
              fontWeight: 700,
              fontFamily: "var(--font-ui)",
              cursor: "pointer",
              boxShadow: "0 2px 8px rgba(8, 16, 32, 0.2)",
            }}
          >
            <DownloadIcon />
            <span>{t("sw.install", "Install")}</span>
          </button>
        )}
        <div
          style={{
            fontSize: 16,
            fontWeight: 800,
            letterSpacing: "0.12em",
            color: "var(--c-ink)",
          }}
        >
          {t("app.title")}
        </div>
      </div>

      {hasCrisis ? (
        <div
          style={{
            margin: "14px 16px 0",
            padding: "10px 14px",
            background: "var(--c-danger-bg)",
            border: "1px solid var(--c-danger)",
            borderRadius: 12,
            display: "flex",
            alignItems: "center",
            gap: 10,
            fontSize: 12,
            color: "var(--c-danger)",
            fontWeight: 600,
          }}
        >
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: 999,
              background: "var(--c-danger)",
              flexShrink: 0,
              animation: "pulseDot 1.4s ease-in-out infinite",
            }}
          />
          <span style={{ minWidth: 0 }}>{t("home.activeCrisis", { name: crisisName })}</span>
          <button
            type="button"
            onClick={onCrisis}
            style={{
              flexShrink: 0,
              marginInlineStart: "auto",
              padding: "6px 12px",
              borderRadius: 999,
              border: "1px solid var(--c-danger)",
              background: "var(--c-card)",
              color: "var(--c-danger)",
              fontSize: 12,
              fontWeight: 700,
              cursor: "pointer",
            }}
          >
            {t("home.changeCrisis")}
          </button>
        </div>
      ) : (
        <button
          type="button"
          onClick={onCrisis}
          style={{
            margin: "14px 16px 0",
            padding: "10px 14px",
            background: "var(--c-blue-50)",
            border: "1px solid var(--c-blue-200)",
            borderRadius: 12,
            display: "flex",
            alignItems: "center",
            gap: 10,
            fontSize: 12,
            color: "var(--c-blue-700)",
            fontWeight: 600,
            cursor: "pointer",
            textAlign: "start",
            fontFamily: "var(--font-ui)",
          }}
        >
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: 999,
              background: "var(--c-blue-700)",
              flexShrink: 0,
            }}
          />
          <span style={{ flex: 1, minWidth: 0 }}>{t("home.selectCrisis")}</span>
          <ArrowRightIcon />
        </button>
      )}

      <div
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          padding:
            "18px calc(16px + env(safe-area-inset-right)) calc(72px + env(safe-area-inset-bottom)) calc(16px + env(safe-area-inset-left))",
          gap: 14,
          minHeight: 0,
        }}
      >
        {/* A div, not <button>, so the "Continue draft" pill can be a real nested button. */}
        {/* biome-ignore lint/a11y/useSemanticElements: needs a nested draft button */}
        <div
          role="button"
          tabIndex={0}
          onClick={onReport}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              onReport();
            }
          }}
          aria-label={t("home.startReport")}
          style={{
            flex: 1,
            minHeight: 160,
            background: "linear-gradient(180deg, var(--c-blue-700) 0%, var(--c-blue-500) 100%)",
            color: "#fff",
            border: 0,
            borderRadius: 22,
            padding: "22px 20px",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            gap: 14,
            textAlign: "center",
            position: "relative",
            overflow: "hidden",
            boxShadow: "0 12px 28px rgba(22,112,194,0.32)",
            cursor: "pointer",
            fontFamily: "var(--font-ui)",
          }}
        >
          <span
            aria-hidden="true"
            style={{
              position: "absolute",
              top: -50,
              insetInlineEnd: -40,
              width: 180,
              height: 180,
              borderRadius: 999,
              border: "2px solid rgba(255,255,255,0.18)",
              pointerEvents: "none",
            }}
          />
          <span
            aria-hidden="true"
            style={{
              position: "absolute",
              top: -20,
              insetInlineEnd: -10,
              width: 130,
              height: 130,
              borderRadius: 999,
              border: "2px solid rgba(255,255,255,0.14)",
              pointerEvents: "none",
            }}
          />
          <div style={{ position: "relative" }}>
            <div style={{ fontSize: 28, fontWeight: 800, lineHeight: 1.1 }}>
              {t("home.reportDamage")}
            </div>
          </div>
          <div
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 8,
              padding: "10px 16px",
              background: "var(--c-card)",
              color: "var(--c-blue-700)",
              borderRadius: 999,
              fontSize: 14,
              fontWeight: 700,
              position: "relative",
            }}
          >
            {t("home.startReport")} <ArrowRightIcon />
          </div>
          {hasDrafts && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                handleContinueDraft();
              }}
              style={{
                position: "relative",
                alignSelf: "center",
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                gap: 6,
                padding: "6px 12px",
                background: "transparent",
                border: 0,
                color: "rgba(255,255,255,0.92)",
                fontSize: 13,
                fontWeight: 700,
                fontFamily: "var(--font-ui)",
                cursor: "pointer",
                whiteSpace: "nowrap",
              }}
            >
              {t("draft.continue")}
              {drafts.length > 1 && (
                <span
                  style={{
                    minWidth: 18,
                    height: 18,
                    padding: "0 5px",
                    borderRadius: 999,
                    background: "rgba(255,255,255,0.25)",
                    color: "#fff",
                    fontSize: 11,
                    fontWeight: 800,
                    display: "inline-flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  {drafts.length}
                </span>
              )}
            </button>
          )}
        </div>

        <button
          type="button"
          onClick={onMap}
          style={{
            flex: 1,
            minHeight: 160,
            background: "var(--c-card)",
            color: "var(--c-ink)",
            border: "2px solid var(--c-blue-200)",
            borderRadius: 22,
            padding: "22px 20px",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            gap: 14,
            textAlign: "center",
            position: "relative",
            overflow: "hidden",
            cursor: "pointer",
            fontFamily: "var(--font-ui)",
          }}
        >
          <span
            aria-hidden="true"
            style={{
              position: "absolute",
              top: -50,
              insetInlineEnd: -40,
              width: 180,
              height: 180,
              borderRadius: 999,
              background: "var(--c-blue-50)",
              pointerEvents: "none",
            }}
          />
          <div style={{ position: "relative" }}>
            <div
              style={{
                fontSize: 28,
                fontWeight: 800,
                lineHeight: 1.1,
                color: "var(--c-ink)",
              }}
            >
              {t("home.liveDamageMap")}
            </div>
          </div>
          <div
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 8,
              padding: "10px 16px",
              background: "var(--c-blue-700)",
              color: "#fff",
              borderRadius: 999,
              fontSize: 14,
              fontWeight: 700,
              position: "relative",
            }}
          >
            {t("home.openMap")} <ArrowRightIcon />
          </div>
          {last24h != null && (
            <div style={{ fontSize: 13, color: "var(--c-ink-3)" }}>
              <b style={{ color: "var(--c-blue-700)" }}>{last24h}</b> {t("home.newTodayLabel")}
            </div>
          )}
        </button>
      </div>
    </div>
  );
}

function DownloadIcon() {
  return (
    <svg aria-hidden="true" width="16" height="16" viewBox="0 0 16 16" fill="none">
      <path
        d="M8 2v8m0 0L4.5 6.5M8 10l3.5-3.5M3 13h10"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
