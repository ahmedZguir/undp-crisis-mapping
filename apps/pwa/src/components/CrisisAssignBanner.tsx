import { useTranslation } from "react-i18next";

interface CrisisAssignBannerProps {
  unassignedCount: number;
  onPickCrisis: () => void;
  onDismiss: () => void;
}

export function CrisisAssignBanner({
  unassignedCount,
  onPickCrisis,
  onDismiss,
}: CrisisAssignBannerProps) {
  const { t } = useTranslation();
  if (unassignedCount <= 0) return null;
  return (
    <div
      role="alert"
      style={{
        position: "fixed",
        top: "calc(env(safe-area-inset-top, 0px) + 56px)",
        insetInlineStart: 12,
        insetInlineEnd: 12,
        zIndex: 1600,
        background: "var(--c-warn-bg, #fff7ed)",
        border: "1px solid var(--c-warn, #b45309)",
        borderRadius: 12,
        padding: "10px 12px",
        display: "flex",
        alignItems: "center",
        gap: 10,
        boxShadow: "0 6px 18px rgba(0,0,0,0.12)",
      }}
    >
      <div style={{ flex: 1, fontSize: 13, color: "var(--c-warn, #92400e)" }}>
        <div style={{ fontWeight: 700 }}>
          {unassignedCount === 1
            ? t("crisisAssign.headingOne", { count: unassignedCount })
            : t("crisisAssign.heading", { count: unassignedCount })}
        </div>
        <div style={{ fontSize: 12, opacity: 0.9 }}>{t("crisisAssign.body")}</div>
      </div>
      <button
        type="button"
        onClick={onPickCrisis}
        style={{
          padding: "8px 12px",
          borderRadius: 8,
          border: "none",
          background: "var(--c-warn, #b45309)",
          color: "#fff",
          fontSize: 13,
          fontWeight: 700,
          cursor: "pointer",
        }}
      >
        {t("crisisAssign.pick")}
      </button>
      <button
        type="button"
        onClick={onDismiss}
        aria-label={t("crisisAssign.dismiss")}
        style={{
          width: 24,
          height: 24,
          borderRadius: 6,
          border: "none",
          background: "transparent",
          color: "var(--c-warn, #92400e)",
          cursor: "pointer",
          fontSize: 18,
          lineHeight: 1,
        }}
      >
        ×
      </button>
    </div>
  );
}
