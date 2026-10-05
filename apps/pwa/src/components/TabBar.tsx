import { type ReactNode, useLayoutEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { TAB_BADGE_BG, type TabBadge } from "../lib/reportNotice";

export type TabId = "report" | "my-reports" | "settings";

interface TabBarProps {
  active: TabId;
  onSelect: (tab: TabId) => void;
  // Null when there is nothing new to surface.
  myReportsBadge?: TabBadge;
}

const TABS: { id: TabId; labelKey: string; icon: ReactNode }[] = [
  {
    id: "report",
    labelKey: "tab.home",
    icon: (
      <svg
        width="22"
        height="22"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <path d="M3 11.5 12 4l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z" />
      </svg>
    ),
  },
  {
    id: "my-reports",
    labelKey: "tab.myReports",
    icon: (
      <svg
        width="22"
        height="22"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
        <polyline points="14 2 14 8 20 8" />
        <line x1="16" y1="13" x2="8" y2="13" />
        <line x1="16" y1="17" x2="8" y2="17" />
        <line x1="10" y1="9" x2="8" y2="9" />
      </svg>
    ),
  },
  {
    id: "settings",
    labelKey: "tab.settings",
    icon: (
      <svg
        width="22"
        height="22"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <circle cx="12" cy="12" r="3" />
        <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
      </svg>
    ),
  },
];

export function TabBar({ active, onSelect, myReportsBadge }: TabBarProps) {
  const { t } = useTranslation();
  const navRef = useRef<HTMLElement>(null);

  // Publish the measured height as --undp-tabbar-h so StepShell sits above the bar.
  // Layout effect so a freshly mounted bar does not flash one frame over the footer.
  useLayoutEffect(() => {
    const el = navRef.current;
    if (!el) return;
    const root = document.documentElement;
    const apply = () => root.style.setProperty("--undp-tabbar-h", `${el.offsetHeight}px`);
    apply();
    // Guarded: no ResizeObserver in jsdom and very old browsers.
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(apply) : null;
    ro?.observe(el);
    return () => {
      ro?.disconnect();
      root.style.removeProperty("--undp-tabbar-h");
    };
  }, []);

  return (
    <nav
      ref={navRef}
      aria-label={t("tab.home")}
      style={{
        position: "fixed",
        left: 0,
        right: 0,
        bottom: 0,
        zIndex: 1400,
        background: "var(--c-card)",
        borderTop: "1px solid var(--c-line)",
        paddingBottom: "env(safe-area-inset-bottom, 0px)",
        display: "flex",
      }}
    >
      {TABS.map((tab) => {
        const selected = active === tab.id;
        const badge = tab.id === "my-reports" ? myReportsBadge : null;
        return (
          <button
            key={tab.id}
            type="button"
            onClick={() => onSelect(tab.id)}
            aria-current={selected ? "page" : undefined}
            style={{
              flex: 1,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: 2,
              padding: "8px 0 6px",
              background: "transparent",
              border: "none",
              cursor: "pointer",
              color: selected ? "var(--c-blue-700, #1d4ed8)" : "var(--c-ink-3, #6b7280)",
              fontSize: 11,
              fontWeight: selected ? 700 : 500,
            }}
          >
            <span style={{ position: "relative", display: "inline-flex" }}>
              {tab.icon}
              {badge && <NoticeDot badge={badge} label={t("tab.unread", "new updates")} />}
            </span>
            <span>{t(tab.labelKey)}</span>
          </button>
        );
      })}
    </nav>
  );
}

// Shows the count when more than one (capped at "9+"), else a plain dot.
function NoticeDot({ badge, label }: { badge: NonNullable<TabBadge>; label: string }) {
  const showCount = badge.count > 1;
  const text = badge.count > 9 ? "9+" : String(badge.count);
  return (
    <span
      aria-label={`${badge.count} ${label}`}
      style={{
        position: "absolute",
        top: -4,
        insetInlineEnd: -6,
        minWidth: showCount ? 15 : 9,
        height: showCount ? 15 : 9,
        padding: showCount ? "0 3px" : 0,
        borderRadius: 999,
        background: TAB_BADGE_BG[badge.color],
        color: "#fff",
        fontSize: 9,
        fontWeight: 800,
        lineHeight: 1,
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        border: "1.5px solid var(--c-card)",
        boxSizing: "border-box",
      }}
    >
      {showCount ? text : ""}
    </span>
  );
}
