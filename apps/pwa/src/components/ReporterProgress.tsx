import type { CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { type Badge, type ReporterStats, levelProgress } from "../api/reporter";
import { BadgeIcon } from "./BadgeIcon";

interface ReporterProgressProps {
  stats: ReporterStats | null;
}

// Gold card chrome shared by the stats card and its no-stats fallback.
const CARD_STYLE: CSSProperties = {
  width: "100%",
  textAlign: "start",
  padding: 12,
  marginBottom: 14,
  background: "linear-gradient(135deg, #fdf6e3, #f8eccd)",
  border: "1px solid #ead8a3",
  borderRadius: 10,
  boxSizing: "border-box",
};

const ICON_DISC_STYLE: CSSProperties = {
  width: 36,
  height: 36,
  borderRadius: 999,
  background: "var(--c-warn)",
  color: "#fff",
  display: "grid",
  placeItems: "center",
  flexShrink: 0,
};

function LevelStar() {
  return (
    <svg width="22" height="22" viewBox="0 0 22 22" fill="var(--c-warn)" aria-hidden="true">
      <path d="M11 2l2.39 5.26L19 8.27l-4 3.89.94 5.5L11 15.27l-4.94 2.39.94-5.5L3 8.27l5.61-.01L11 2Z" />
    </svg>
  );
}

function NewBadgePop({ badge }: { badge: Badge }) {
  const { t } = useTranslation();
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "8px 10px",
        background: "rgba(0,0,0,0.06)",
        borderRadius: 8,
        marginBottom: 8,
        animation: "badge-pop 0.4s ease-out",
      }}
    >
      <div
        style={{
          width: 28,
          height: 28,
          borderRadius: 999,
          background: "var(--c-warn)",
          color: "#fff",
          display: "grid",
          placeItems: "center",
          flexShrink: 0,
        }}
      >
        <BadgeIcon slug={badge.slug} size={14} />
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 11, fontWeight: 700, color: "#5d4513" }}>
          {t("confirmation.newBadge", {
            name: badge.name,
            defaultValue: `New badge: ${badge.name}`,
          })}
        </div>
      </div>
    </div>
  );
}

export function ReporterProgress({ stats }: ReporterProgressProps) {
  const { t } = useTranslation();

  if (!stats) {
    return <StaticFallback />;
  }

  const { total_reports, points, newly_earned } = stats;
  const { level, progress, remaining } = levelProgress(points);
  const topBadge = newly_earned[0] ?? null;
  const extraCount = newly_earned.length - 1;

  return (
    <div style={CARD_STYLE}>
      <div
        style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: topBadge ? 8 : 10 }}
      >
        <div style={ICON_DISC_STYLE}>
          <BadgeIcon slug={topBadge?.slug ?? "active_responder"} size={18} />
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: "#5d4513" }}>
            {t("myStats.level", { level, defaultValue: `Level ${level}` })}
          </div>
          <div style={{ fontSize: 11, color: "#7a5d2c" }}>
            {t("myStats.reports", { defaultValue: "Reports" })}: {total_reports} ·{" "}
            {t("myStats.points", { defaultValue: "Points" })}: {points}
          </div>
        </div>
        <LevelStar />
      </div>

      {topBadge && <NewBadgePop badge={topBadge} />}
      {extraCount > 0 && (
        <div style={{ fontSize: 10, color: "#7a5d2c", marginBottom: 8 }}>
          +{extraCount} more badge{extraCount > 1 ? "s" : ""}
        </div>
      )}

      <div
        style={{
          display: "flex",
          gap: 4,
          height: 4,
          borderRadius: 999,
          overflow: "hidden",
          background: "rgba(0,0,0,0.06)",
        }}
      >
        <div
          style={{
            width: `${Math.min(progress * 100, 100)}%`,
            background: "var(--c-warn)",
            borderRadius: 999,
            transition: "width 0.6s ease-out",
          }}
        />
      </div>
      <div style={{ fontSize: 10, color: "#7a5d2c", marginTop: 6 }}>
        {t("myStats.toNextLevel", {
          count: remaining,
          level: level + 1,
          defaultValue: `${remaining} more point${remaining !== 1 ? "s" : ""} to Level ${level + 1}`,
        })}
      </div>

      <style>{`
        @keyframes badge-pop {
          0%   { transform: scale(0.7); opacity: 0; }
          60%  { transform: scale(1.08); }
          100% { transform: scale(1); opacity: 1; }
        }
      `}</style>
    </div>
  );
}

function StaticFallback() {
  const { t } = useTranslation();
  return (
    <div style={CARD_STYLE}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
        <div style={ICON_DISC_STYLE}>
          <BadgeIcon slug="active_responder" size={20} />
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: "#5d4513" }}>
            {t("myStats.startTitle", { defaultValue: "Your contributions" })}
          </div>
          <div style={{ fontSize: 11, color: "#7a5d2c" }}>
            {t("myStats.level", { level: 1, defaultValue: "Level 1" })}
          </div>
        </div>
        <LevelStar />
      </div>
      <div
        style={{
          display: "flex",
          gap: 4,
          height: 4,
          borderRadius: 999,
          overflow: "hidden",
          background: "rgba(0,0,0,0.06)",
        }}
      >
        <div style={{ width: "20%", background: "var(--c-warn)", borderRadius: 999 }} />
      </div>
      <div style={{ fontSize: 10, color: "#7a5d2c", marginTop: 6 }}>
        {t("myStats.startBody", { defaultValue: "Submit a report to start earning badges" })}
      </div>
    </div>
  );
}
