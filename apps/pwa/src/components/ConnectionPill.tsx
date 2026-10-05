import { useTranslation } from "react-i18next";
import { useOnlineStatus } from "../hooks/useOnlineStatus";

function WifiIcon({ online, size }: { online: boolean; size: number }) {
  // Outer two arcs fade out when offline; the inner arc and dot stay solid.
  const arcStroke = online ? "currentColor" : "rgba(0,0,0,0.18)";
  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M2 8.5a15 15 0 0 1 20 0" stroke={arcStroke} />
      <path d="M5 12a11 11 0 0 1 14 0" stroke={arcStroke} />
      <path d="M8.5 15.5a6 6 0 0 1 7 0" stroke="currentColor" />
      <circle cx="12" cy="19" r="1" fill="currentColor" stroke="none" />
      {online ? null : <line x1="3" y1="3" x2="21" y2="21" stroke="currentColor" strokeWidth="2" />}
    </svg>
  );
}

export function ConnectionPill() {
  const { t } = useTranslation();
  const online = useOnlineStatus();

  const color = online ? "var(--c-success, #1f9d55)" : "var(--c-danger)";
  const label = online
    ? t("connection.online", { defaultValue: "Online" })
    : t("connection.offline", { defaultValue: "Offline" });

  return (
    <output
      aria-label={label}
      title={label}
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        width: 22,
        height: 22,
        color,
        flexShrink: 0,
      }}
    >
      <WifiIcon online={online} size={18} />
    </output>
  );
}
