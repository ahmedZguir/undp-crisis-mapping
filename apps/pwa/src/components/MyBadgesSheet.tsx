import { useTranslation } from "react-i18next";
import { BADGE_SLUGS, type Badge, type ReporterStats, levelProgress } from "../api/reporter";
import { BadgeIcon } from "./BadgeIcon";

// `earned` is null while the badge is locked; locked rows show the criteria.
function BadgeRow({ slug, earned }: { slug: string; earned: Badge | null }) {
  const { t } = useTranslation();
  const label = t(`badges.${slug}`, { defaultValue: earned?.name ?? slug });
  const description = t(`badgeDesc.${slug}`, { defaultValue: "" });
  const isEarned = earned !== null;
  const meta = isEarned
    ? t("myStats.earned", {
        date: new Date(earned.earned_at).toLocaleDateString(),
        defaultValue: `Earned ${new Date(earned.earned_at).toLocaleDateString()}`,
      })
    : t("myStats.locked", { defaultValue: "Not earned yet" });
  return (
    <div
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 12,
        padding: "12px 0",
        borderBottom: "1px solid var(--c-line)",
        opacity: isEarned ? 1 : 0.55,
      }}
    >
      <div
        style={{
          width: 38,
          height: 38,
          borderRadius: 999,
          background: isEarned ? "linear-gradient(135deg, #f8eccd, #e8d090)" : "var(--c-surface)",
          border: isEarned ? "none" : "1px solid var(--c-line)",
          color: isEarned ? "#b5801a" : "var(--c-ink-3)",
          display: "grid",
          placeItems: "center",
          flexShrink: 0,
        }}
      >
        <BadgeIcon slug={slug} size={18} />
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 14, fontWeight: 600, color: "var(--c-ink)" }}>{label}</div>
        {description && (
          <div style={{ fontSize: 12, color: "var(--c-ink-2)", marginTop: 2, lineHeight: 1.4 }}>
            {description}
          </div>
        )}
        <div style={{ fontSize: 11, color: "var(--c-ink-3)", marginTop: 3 }}>{meta}</div>
      </div>
    </div>
  );
}

const KNOWN_SLUGS = new Set<string>(BADGE_SLUGS);

interface MyBadgesSheetProps {
  stats: ReporterStats | null;
  onClose: () => void;
}

export function MyBadgesSheet({ stats, onClose }: MyBadgesSheetProps) {
  const { t } = useTranslation();

  const points = stats?.points ?? 0;
  const total = stats?.total_reports ?? 0;
  const badges = stats?.badges ?? [];
  const { level, progress, remaining } = levelProgress(points);

  // Earned first; earned slugs missing from the catalog (future/legacy) are appended.
  const earnedBySlug = new Map(badges.map((b) => [b.slug, b]));
  const extraSlugs = badges.map((b) => b.slug).filter((s) => !KNOWN_SLUGS.has(s));
  const catalog = [...BADGE_SLUGS, ...extraSlugs];
  const orderedSlugs = [
    ...catalog.filter((s) => earnedBySlug.has(s)),
    ...catalog.filter((s) => !earnedBySlug.has(s)),
  ];

  return (
    <>
      <button
        type="button"
        onClick={onClose}
        style={{
          position: "fixed",
          inset: 0,
          background: "rgba(8,16,32,0.45)",
          zIndex: 1900,
          border: "none",
          cursor: "default",
          padding: 0,
        }}
        aria-label="Close"
      />
      <div
        // biome-ignore lint/a11y/useSemanticElements: a bottom sheet animated via CSS transform is not a native <dialog> (which needs showModal()).
        role="dialog"
        aria-label={t("myStats.title", { defaultValue: "My Contributions" })}
        style={{
          position: "fixed",
          bottom: 0,
          left: 0,
          right: 0,
          zIndex: 1901,
          background: "var(--c-card)",
          borderRadius: "18px 18px 0 0",
          padding: "0 18px calc(env(safe-area-inset-bottom, 12px) + 16px)",
          maxHeight: "70vh",
          overflowY: "auto",
          WebkitOverflowScrolling: "touch",
          boxShadow: "0 -4px 24px rgba(0,0,0,0.12)",
          animation: "sheet-slide-up 0.28s ease-out",
        }}
      >
        <div
          style={{
            width: 36,
            height: 4,
            borderRadius: 2,
            background: "var(--c-line)",
            margin: "12px auto 16px",
          }}
        />

        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: 14,
          }}
        >
          <div style={{ fontSize: 18, fontWeight: 700, color: "var(--c-ink)" }}>
            {t("myStats.title", { defaultValue: "My Contributions" })}
          </div>
          <button
            type="button"
            onClick={onClose}
            style={{
              width: 32,
              height: 32,
              border: 0,
              background: "transparent",
              cursor: "pointer",
              display: "grid",
              placeItems: "center",
              color: "var(--c-ink-2)",
            }}
            aria-label="Close"
          >
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
              <path
                d="M1 1l12 12M13 1L1 13"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </div>

        <div
          style={{
            display: "flex",
            gap: 10,
            marginBottom: 16,
          }}
        >
          {[
            { label: t("myStats.reports", { defaultValue: "Reports" }), value: total },
            { label: t("myStats.points", { defaultValue: "Points" }), value: points },
            { label: t("myStats.badges", { defaultValue: "Badges" }), value: badges.length },
          ].map((item) => (
            <div
              key={item.label}
              style={{
                flex: 1,
                background: "var(--c-surface)",
                border: "1px solid var(--c-line)",
                borderRadius: 10,
                padding: "10px 8px",
                textAlign: "center",
              }}
            >
              <div style={{ fontSize: 22, fontWeight: 800, color: "var(--c-ink)" }}>
                {item.value}
              </div>
              <div style={{ fontSize: 10, color: "var(--c-ink-3)", marginTop: 2 }}>
                {item.label}
              </div>
            </div>
          ))}
        </div>

        <div style={{ marginBottom: 16 }}>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "baseline",
              marginBottom: 6,
            }}
          >
            <span style={{ fontSize: 13, fontWeight: 700, color: "var(--c-ink)" }}>
              {t("myStats.level", { level, defaultValue: `Level ${level}` })}
            </span>
            <span style={{ fontSize: 10, color: "var(--c-ink-3)" }}>
              {t("myStats.toNextLevel", {
                count: remaining,
                level: level + 1,
                defaultValue: `${remaining} more point${remaining !== 1 ? "s" : ""} to Level ${level + 1}`,
              })}
            </span>
          </div>
          <div
            style={{
              height: 6,
              borderRadius: 999,
              overflow: "hidden",
              background: "var(--c-line)",
            }}
          >
            <div
              style={{
                width: `${Math.min(progress * 100, 100)}%`,
                height: "100%",
                background: "var(--c-warn)",
                borderRadius: 999,
              }}
            />
          </div>
        </div>

        {/* Mirrors api/reports/quality.py::compute_points. */}
        <div
          style={{
            background: "var(--c-surface)",
            border: "1px solid var(--c-line)",
            borderRadius: 10,
            padding: "12px 14px",
            marginBottom: 16,
          }}
        >
          <div style={{ fontSize: 13, fontWeight: 600, color: "var(--c-ink)", marginBottom: 4 }}>
            {t("myStats.pointsTitle", { defaultValue: "How points work" })}
          </div>
          <div style={{ fontSize: 12, color: "var(--c-ink-2)", marginBottom: 8, lineHeight: 1.4 }}>
            {t("myStats.pointsIntro", { defaultValue: "Each report earns up to 5 points:" })}
          </div>
          {[
            "myStats.pointsReport",
            "myStats.pointsPhoto",
            "myStats.pointsDescription",
            "myStats.pointsRelevant",
            "myStats.pointsDamage",
          ].map((key) => (
            <div
              key={key}
              style={{ display: "flex", gap: 8, alignItems: "baseline", marginBottom: 4 }}
            >
              <span
                style={{ fontSize: 11, fontWeight: 800, color: "var(--c-warn)", flexShrink: 0 }}
              >
                +1
              </span>
              <span style={{ fontSize: 12, color: "var(--c-ink-2)", lineHeight: 1.4 }}>
                {t(key, { defaultValue: "" })}
              </span>
            </div>
          ))}
          <div style={{ fontSize: 11, color: "var(--c-ink-3)", marginTop: 6, lineHeight: 1.4 }}>
            {t("myStats.pointsNote", { defaultValue: "Duplicate photos earn no points." })}
          </div>
        </div>

        <div style={{ fontSize: 13, fontWeight: 600, color: "var(--c-ink-2)", marginBottom: 4 }}>
          {t("myStats.howToEarn", { defaultValue: "Badges & how to earn them" })}
        </div>
        <div>
          {orderedSlugs.map((slug) => (
            <BadgeRow key={slug} slug={slug} earned={earnedBySlug.get(slug) ?? null} />
          ))}
        </div>
      </div>

      <style>{`
        @keyframes sheet-slide-up {
          from { transform: translateY(100%); }
          to   { transform: translateY(0); }
        }
      `}</style>
    </>
  );
}
