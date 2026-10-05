// Shown above the report flow in preview mode (?preview=1).
import { useTranslation } from "react-i18next";

export function PreviewBanner() {
  const { t } = useTranslation();
  return (
    <output
      style={{
        position: "sticky",
        top: 0,
        zIndex: 2000,
        padding: "8px 12px",
        background: "#fde68a",
        color: "#78350f",
        fontWeight: 600,
        textAlign: "center",
      }}
    >
      {t("preview.banner", { defaultValue: "Preview mode: changes not saved." })}
    </output>
  );
}
