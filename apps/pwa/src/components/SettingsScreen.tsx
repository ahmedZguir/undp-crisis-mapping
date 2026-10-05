import type React from "react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { deleteAllReports } from "../api/reports";
import { useIsRtl } from "../hooks/useIsRtl";
import { useTheme } from "../hooks/useTheme";
import { clearCitizenLocalData } from "../lib/citizenData";
import { getClientId } from "../lib/clientId";
import { clearAllReports } from "../lib/db";
import { LANGUAGES } from "../lib/langGate";
import type { Crisis } from "../types";
import { ConfirmDialog } from "./ui/ConfirmDialog";

interface SettingsScreenProps {
  onBack: () => void;
  onChangeLanguage: () => void;
  onChangeCrisis: () => void;
  onPrivacy: () => void;
  activeCrisis: Crisis | null;
}

export function SettingsScreen({
  onBack,
  onChangeLanguage,
  onChangeCrisis,
  onPrivacy,
  activeCrisis,
}: SettingsScreenProps) {
  const { t, i18n } = useTranslation();
  const isRtl = useIsRtl();
  const { theme, toggle: toggleTheme } = useTheme();
  const [clientId, setClientId] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [deleted, setDeleted] = useState(false);
  const langCode = i18n.language || "en";
  const langLabel = LANGUAGES.find((l) => l.code === langCode)?.native ?? langCode;

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const id = await getClientId();
      if (!cancelled) setClientId(id);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Auto-back from the success screen after ~5 seconds. The user can also
  // tap "Back" earlier; we treat both as the same dismissal.
  useEffect(() => {
    if (!deleted) return;
    const handle = window.setTimeout(() => setDeleted(false), 5000);
    return () => window.clearTimeout(handle);
  }, [deleted]);

  const handleDeleteReports = async () => {
    // client_id is deliberately not rotated: it is stable for the lifetime
    // of this device install.
    //   1. Ask the server to forget us (best-effort; the endpoint always
    //      returns 200 so it is not an ownership oracle).
    //   2. Wipe IndexedDB and the personal localStorage keys.
    //   3. Show a brief "Your data has been deleted." success screen.
    setClearing(true);
    try {
      // Best-effort server-side wipe: always-200 contract, so we ignore the
      // outcome. The local IDB wipe below is the source of truth for this
      // device; a transient network failure must not block the user.
      const id = await getClientId().catch(() => null);
      if (id) await deleteAllReports(id);
      await clearAllReports();
      clearCitizenLocalData();
      setDeleted(true);
    } catch (err) {
      // No success screen: the device still holds the data.
      console.error("[settings] delete my data failed", err);
    } finally {
      setClearing(false);
      setConfirmOpen(false);
    }
  };

  if (deleted) {
    return <DeletedScreen onBack={() => setDeleted(false)} />;
  }

  return (
    <div
      style={{
        minHeight: "100svh",
        display: "flex",
        flexDirection: "column",
        background: "var(--c-surface)",
        fontFamily: "var(--font-ui)",
        textAlign: "start",
      }}
    >
      <div
        style={{
          padding:
            "max(env(safe-area-inset-top, 18px), 18px) calc(22px + env(safe-area-inset-right)) 14px calc(22px + env(safe-area-inset-left))",
          display: "flex",
          alignItems: "center",
          gap: 10,
          borderBottom: "1px solid var(--c-line-2)",
          background: "var(--c-card)",
        }}
      >
        <button
          type="button"
          onClick={onBack}
          aria-label={t("nav.back", { defaultValue: "Back" })}
          style={{
            width: 36,
            height: 36,
            border: 0,
            background: "transparent",
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: 0,
          }}
        >
          <svg
            width="15"
            height="15"
            viewBox="0 0 18 18"
            fill="none"
            aria-hidden="true"
            style={isRtl ? { transform: "scaleX(-1)" } : undefined}
          >
            <path
              d="M11 4L6 9l5 5"
              stroke="var(--c-ink)"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <GearIcon />
          <div style={{ fontSize: 16, fontWeight: 800, color: "var(--c-ink)" }}>
            {t("settings.title", { defaultValue: "Settings" })}
          </div>
        </div>
      </div>

      <div
        style={{
          // Clear the fixed tab bar (--undp-tabbar-h).
          padding: "16px 16px calc(16px + var(--undp-tabbar-h, 0px))",
          display: "flex",
          flexDirection: "column",
          gap: 16,
        }}
      >
        <SettingsRow
          icon={<GlobeIcon />}
          title={t("settings.language.title", { defaultValue: "Language" })}
          value={langLabel}
          actionLabel={t("settings.language.change", { defaultValue: "Change" })}
          onAction={onChangeLanguage}
        />

        <SettingsRow
          icon={theme === "dark" ? <MoonIcon /> : <SunIcon />}
          title={t("settings.theme.title", { defaultValue: "Theme" })}
          value={
            theme === "dark"
              ? t("settings.theme.dark", { defaultValue: "Dark" })
              : t("settings.theme.light", { defaultValue: "Light" })
          }
          actionLabel={
            theme === "dark"
              ? t("settings.theme.switchToLight", { defaultValue: "Switch to light" })
              : t("settings.theme.switchToDark", { defaultValue: "Switch to dark" })
          }
          onAction={toggleTheme}
        />

        <SettingsRow
          icon={<AlertIcon />}
          title={t("settings.crisis.title", { defaultValue: "Active crisis" })}
          value={activeCrisis?.name ?? t("settings.crisis.none", { defaultValue: "None selected" })}
          actionLabel={
            activeCrisis
              ? t("settings.crisis.change", { defaultValue: "Change" })
              : t("settings.crisis.select", { defaultValue: "Select" })
          }
          onAction={onChangeCrisis}
        />

        <SettingsRow
          icon={<ShieldIcon />}
          title={t("settings.privacy.title", { defaultValue: "Privacy notice" })}
          value={t("settings.privacy.value", {
            defaultValue: "How your information is handled",
          })}
          actionLabel={t("settings.privacy.view", { defaultValue: "View" })}
          onAction={onPrivacy}
        />

        <section
          style={{
            padding: 14,
            borderRadius: 12,
            border: "1px solid var(--c-line-2)",
            background: "var(--c-card)",
          }}
        >
          <div style={{ fontSize: 13, fontWeight: 700, color: "var(--c-ink)" }}>
            {t("settings.clientId.title", { defaultValue: "Client ID" })}
          </div>
          <div
            style={{
              marginTop: 6,
              fontSize: 12,
              color: "var(--c-ink-3)",
              lineHeight: 1.5,
            }}
          >
            {t("settings.clientId.body", {
              defaultValue: "Anonymous identifier this device sends with every report.",
            })}
          </div>
          <div
            style={{
              marginTop: 10,
              padding: "8px 10px",
              fontSize: 12,
              fontFamily: "var(--font-mono, monospace)",
              background: "var(--c-blue-50)",
              border: "1px solid var(--c-blue-200)",
              borderRadius: 8,
              color: "var(--c-blue-800)",
              wordBreak: "break-all",
            }}
          >
            {clientId ?? "…"}
          </div>
        </section>

        <section
          style={{
            padding: 14,
            borderRadius: 12,
            border: "1px solid var(--c-line-2)",
            background: "var(--c-card)",
          }}
        >
          <div style={{ fontSize: 13, fontWeight: 700, color: "var(--c-ink)" }}>
            {t("settings.deleteReports.title", { defaultValue: "Delete all my reports" })}
          </div>
          <div
            style={{
              marginTop: 6,
              fontSize: 12,
              color: "var(--c-ink-3)",
              lineHeight: 1.5,
            }}
          >
            {t("settings.deleteReports.body", {
              defaultValue:
                "Permanently deletes every report and photo you have submitted under any crisis and wipes drafts and queued reports from this device. This cannot be undone.",
            })}
          </div>
          <button
            type="button"
            onClick={() => setConfirmOpen(true)}
            disabled={clearing}
            style={{
              marginTop: 12,
              padding: "10px 14px",
              borderRadius: 10,
              border: "1px solid var(--c-danger)",
              background: "var(--c-card)",
              color: "var(--c-danger)",
              fontSize: 13,
              fontWeight: 700,
              cursor: clearing ? "not-allowed" : "pointer",
            }}
          >
            {t("settings.deleteReports.action", { defaultValue: "Delete all my reports" })}
          </button>
        </section>
      </div>

      {confirmOpen && (
        <ConfirmDialog
          titleId="clear-reports-title"
          title={t("settings.deleteReports.confirmTitle", {
            defaultValue: "Delete all your reports?",
          })}
          body={t("settings.deleteReports.confirmBody", {
            defaultValue:
              "This will permanently delete every report and photo you have submitted under any crisis. This cannot be undone. Continue?",
          })}
          cancelLabel={t("settings.deleteReports.cancel", { defaultValue: "Cancel" })}
          confirmLabel={
            clearing
              ? t("settings.deleteReports.deleting", { defaultValue: "Deleting…" })
              : t("settings.deleteReports.confirm", { defaultValue: "Delete everything" })
          }
          busy={clearing}
          onCancel={() => setConfirmOpen(false)}
          onConfirm={() => void handleDeleteReports()}
        />
      )}
    </div>
  );
}

interface DeletedScreenProps {
  onBack: () => void;
}

// Brief success confirmation shown after the user completes the
// "Delete all my reports" flow. Auto-dismisses after ~5 seconds via the
// parent's `setTimeout`; the user can also tap "Back" to dismiss
// immediately. Kept inline rather than its own file: it is exclusively
// reachable from the SettingsScreen flow, and the markup is shorter than
// the boilerplate of a separate component.
function DeletedScreen({ onBack }: DeletedScreenProps) {
  const { t } = useTranslation();
  return (
    <div
      style={{
        minHeight: "100svh",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
        background: "var(--c-surface)",
        fontFamily: "var(--font-ui)",
        textAlign: "center",
        gap: 18,
      }}
    >
      <div
        aria-hidden
        style={{
          width: 64,
          height: 64,
          borderRadius: 999,
          background: "var(--c-green-50, #ecfdf5)",
          color: "var(--c-green-700, #047857)",
          display: "grid",
          placeItems: "center",
          fontSize: 32,
          fontWeight: 800,
        }}
      >
        ✓
      </div>
      <div style={{ fontSize: 18, fontWeight: 800, color: "var(--c-ink)" }}>
        {t("settings.deleteReports.successTitle", {
          defaultValue: "Your data has been deleted.",
        })}
      </div>
      <div
        style={{
          fontSize: 13,
          color: "var(--c-ink-2)",
          maxWidth: 320,
          lineHeight: 1.5,
        }}
      >
        {t("settings.deleteReports.successBody", {
          defaultValue:
            "Your reports, photos, and local history have been removed from this device.",
        })}
      </div>
      <button
        type="button"
        onClick={onBack}
        style={{
          marginTop: 6,
          padding: "10px 22px",
          borderRadius: 10,
          border: "1px solid var(--c-line)",
          background: "#fff",
          color: "var(--c-ink)",
          fontSize: 13,
          fontWeight: 700,
          cursor: "pointer",
        }}
      >
        {t("settings.deleteReports.successBack", { defaultValue: "Back to settings" })}
      </button>
    </div>
  );
}

interface SettingsRowProps {
  title: string;
  value: string;
  actionLabel: string;
  onAction: () => void;
  icon?: React.ReactNode;
}

function SettingsRow({ title, value, actionLabel, onAction, icon }: SettingsRowProps) {
  return (
    <section
      style={{
        padding: 14,
        borderRadius: 12,
        border: "1px solid var(--c-line-2)",
        background: "var(--c-card)",
        display: "flex",
        alignItems: "center",
        gap: 12,
      }}
    >
      {icon && (
        <div
          aria-hidden
          style={{
            width: 36,
            height: 36,
            borderRadius: 10,
            background: "var(--c-blue-50)",
            color: "var(--c-blue-700)",
            display: "grid",
            placeItems: "center",
            flexShrink: 0,
          }}
        >
          {icon}
        </div>
      )}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: "var(--c-ink)" }}>{title}</div>
        <div
          style={{
            marginTop: 4,
            fontSize: 12,
            color: "var(--c-ink-3)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {value}
        </div>
      </div>
      <button
        type="button"
        onClick={onAction}
        style={{
          padding: "8px 12px",
          borderRadius: 8,
          border: "1px solid var(--c-line)",
          background: "var(--c-card)",
          fontSize: 12,
          fontWeight: 700,
          color: "var(--c-ink)",
          cursor: "pointer",
          flexShrink: 0,
        }}
      >
        {actionLabel}
      </button>
    </section>
  );
}

function GearIcon() {
  return (
    <svg
      aria-hidden="true"
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
    </svg>
  );
}

function GlobeIcon() {
  return (
    <svg
      aria-hidden="true"
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18" />
      <path d="M12 3c2.7 3 2.7 15 0 18M12 3c-2.7 3-2.7 15 0 18" />
    </svg>
  );
}

function SunIcon() {
  return (
    <svg
      aria-hidden="true"
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg
      aria-hidden="true"
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
    </svg>
  );
}

function ShieldIcon() {
  return (
    <svg
      aria-hidden="true"
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M12 3l8 3v6c0 5-3.5 7.5-8 9-4.5-1.5-8-4-8-9V6l8-3z" />
      <path d="M9 12l2 2 4-4" />
    </svg>
  );
}

function AlertIcon() {
  return (
    <svg
      aria-hidden="true"
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M10.3 3.86a2 2 0 0 1 3.4 0l8.5 14.14A2 2 0 0 1 20.5 21h-17a2 2 0 0 1-1.7-3l8.5-14.14z" />
      <path d="M12 9v4M12 17h.01" />
    </svg>
  );
}
